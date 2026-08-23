try:
    from agents import RunResult, RunHooks
    from agents.models.interface import Model
    from agents.exceptions import ModelBehaviorError, MaxTurnsExceeded
    from agents.run import AgentRunner
except ImportError:
    # Create dummy classes to allow the application to run
    # This is a workaround for the missing 'agents' module
    class RunResult:
        pass
    class RunHooks:
        pass
    class Model:
        pass
    class ModelBehaviorError(Exception):
        pass
    class MaxTurnsExceeded(Exception):
        pass
    class AgentRunner:
        async def run(self, *args, **kwargs):
            print("Warning: 'agents' module not found. Using dummy AgentRunner.")
            raise NotImplementedError("AgentRunner is not implemented")

from typing import Any, Dict, List, Optional, Union
import asyncio
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Custom runner that extends AgentRunner and adds fallback logic
class FallbackAgentRunner(AgentRunner):
    def __init__(self):
        super().__init__()
        # LLM model configurations. Quota is tracked per model name below,
        # not per provider -- Gemini's two models here have very different
        # daily limits from each other (see the comment on model_limits).
        self.LLM_MODELS = [
            # --- Gemini free tier: each model has its OWN independent daily
            # request quota (RPD). Confirmed from Google AI Studio dashboard:
            # Flash variants = 20 RPD / 5 RPM each; Lite variants = 500 RPD /
            # 15 RPM each. Listing them as separate entries so the fallback
            # chain can exhaust one model's quota before moving to the next,
            # instead of sharing a single bucket.
            {"name": "gemini-flash-latest", "model": "gemini-flash-latest", "provider": "gemini"},
            {"name": "gemini-3.6-flash", "model": "gemini-3.6-flash", "provider": "gemini"},
            {"name": "gemini-3.5-flash", "model": "gemini-3.5-flash", "provider": "gemini"},
            {"name": "gemini-3.5-flash-lite", "model": "gemini-3.5-flash-lite", "provider": "gemini"},
            {"name": "gemini-3.1-flash-lite", "model": "gemini-3.1-flash-lite", "provider": "gemini"},
            # DeepSeek V4 Flash via OpenRouter (paid, pay-per-token). Used as
            # the preferred evaluation model for cross-provider bias: when a
            # Gemini model writes content, DeepSeek evaluates it, and vice
            # versa. Also acts as a general-purpose fallback when Gemini
            # quota is exhausted. Cost: ~$0.07/M input tokens -- negligible
            # for the few evaluation calls per content piece.
            {"name": "deepseek-v4-flash", "model": "deepseek/deepseek-v4-flash-0731", "provider": "openrouter-paid"},
            # OpenRouter free tier auto-router: resolves to whatever free
            # model is currently available. 50 RPD without purchased credits.
            {"name": "openrouter-free", "model": "openrouter/free", "provider": "openrouter"},
        ]

        # Paid, last-resort-only models. DeepSeek V4 Flash has been promoted
        # to LLM_MODELS (for cross-provider evaluation). This list is now
        # empty but kept for structural compatibility with run_with_fallback.
        self.LAST_RESORT_MODELS = []

        # Quota tracking is keyed by MODEL NAME, not provider. Each Gemini
        # model has its own independent daily quota (confirmed from Google
        # AI Studio dashboard). DeepSeek is pay-per-token (no real daily
        # cap -- the number is a sanity ceiling). openrouter-free is 50/day
        # without purchased credits.
        self.model_usage = {m["name"]: 0 for m in self.LLM_MODELS}

        # Per-model daily request limits (RPD) from Google AI Studio dashboard.
        self.model_limits = {
            "gemini-flash-latest": 20,
            "gemini-3.6-flash": 20,
            "gemini-3.5-flash": 20,
            "gemini-3.5-flash-lite": 500,
            "gemini-3.1-flash-lite": 500,
            "deepseek-v4-flash": 1000,
            "openrouter-free": 50,
        }

        # Per-model requests-per-minute (RPM) limits.
        self.model_rpm_limits = {
            "gemini-flash-latest": 5,
            "gemini-3.6-flash": 5,
            "gemini-3.5-flash": 5,
            "gemini-3.5-flash-lite": 15,
            "gemini-3.1-flash-lite": 15,
            "deepseek-v4-flash": 60,
            "openrouter-free": 20,
        }

        self.last_reset = datetime.now()

        # Sliding 60-second window for RPM tracking: model_name -> list of
        # timestamps of recent calls. Cleaned up on each is_model_available
        # check so stale entries don't accumulate.
        self.rpm_timestamps: Dict[str, list] = {m["name"]: [] for m in self.LLM_MODELS}

        # Per-model performance tracking (used to sort which model to try first)
        self.provider_stats = {m["name"]: {"success_count": 0, "error_count": 0, "avg_response_time": 0.0} for m in self.LLM_MODELS}

        # Temporary per-model unavailability tracking
        self.provider_unavailable_until = {m["name"]: None for m in self.LLM_MODELS}

        # Seed model_usage from today's model_usage_log sheet entries so
        # quota tracking survives across process restarts (each GitHub Actions
        # run starts a fresh process with in-memory counters at zero).
        self._seed_usage_from_sheet()

    def get_gemini_client(self):
        from agents import AsyncOpenAI
        return AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=os.environ['GEMINI_API_KEY']
        )

    def get_openrouter_client(self):
        from agents import AsyncOpenAI
        return AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ['OPENROUTER_API_KEY']
        )

    def get_model_by_name(self, model_name):
        """Returns the model configuration for the specified model name."""
        from agents import OpenAIChatCompletionsModel
        
        for model_config in self.LLM_MODELS:
            if model_config["name"] == model_name:
                # Validate model name is not empty
                if not model_config["model"] or model_config["model"].strip() == "":
                    raise ValueError(f"Model config for {model_name} has empty model string!")
                
                print(f"Creating model: {model_name} -> {model_config['model']}")
                
                # Get client if it's a function, otherwise use the provider
                if "client" in model_config and callable(model_config["client"]):
                    client = model_config["client"]()
                else:
                    if model_config["provider"] == "gemini":
                        client = self.get_gemini_client()
                    elif model_config["provider"] in ("openrouter", "openrouter-paid"):
                        client = self.get_openrouter_client()
                    else:
                        raise ValueError(f"Unknown provider: {model_config['provider']}")

                return OpenAIChatCompletionsModel(
                    model=model_config["model"],
                    openai_client=client
                )

        # Last-resort models are deliberately excluded from LLM_MODELS (see
        # comment where LAST_RESORT_MODELS is defined) but still need to
        # resolve here, since run_with_fallback looks them up by name too.
        for model_config in self.LAST_RESORT_MODELS:
            if model_config["name"] == model_name:
                client = self.get_openrouter_client()
                print(f"Creating model: {model_name} -> {model_config['model']} (last resort)")
                return OpenAIChatCompletionsModel(
                    model=model_config["model"],
                    openai_client=client
                )

        # If not found, raise error instead of defaulting
        available_models = [m["name"] for m in self.LLM_MODELS + self.LAST_RESORT_MODELS]
        raise ValueError(f"Model name '{model_name}' not found in LLM_MODELS. Available models: {available_models}")

    async def is_model_available(self, model_name):
        """Check if this specific model has quota remaining (both RPM and
        RPD) and is not temporarily unavailable. Keyed by model name, not
        provider -- see the comment on model_limits for why."""
        # Reset usage daily
        if (datetime.now() - self.last_reset).days >= 1:
            print(f"Resetting daily usage counters for all models")
            self.model_usage = {name: 0 for name in self.model_usage}
            self.last_reset = datetime.now()

        # Check if this model is temporarily unavailable
        if self.provider_unavailable_until.get(model_name) is not None:
            if datetime.now() < self.provider_unavailable_until[model_name]:
                print(f"Model {model_name} is temporarily unavailable")
                return False
            else:
                self.provider_unavailable_until[model_name] = None

        now = datetime.now()
        current_usage = self.model_usage.get(model_name, 0)
        rpd_limit = self.model_limits.get(model_name, 0)
        rpm_limit = self.model_rpm_limits.get(model_name, 60)

        # Clean stale timestamps from the RPM sliding window (>60s old)
        cutoff = now - timedelta(seconds=60)
        self.rpm_timestamps.setdefault(model_name, [])
        self.rpm_timestamps[model_name] = [
            ts for ts in self.rpm_timestamps[model_name] if ts > cutoff
        ]
        current_rpm = len(self.rpm_timestamps[model_name])

        rpd_ok = current_usage < rpd_limit - 5
        rpm_ok = current_rpm < rpm_limit
        available = rpd_ok and rpm_ok

        print(f"Model {model_name}: {current_usage}/{rpd_limit} RPD, {current_rpm}/{rpm_limit} RPM, available: {available}")
        return available

    async def increment_usage(self, model_name):
        """Increment usage, track RPM, and log to Google Sheet."""
        if model_name in self.model_usage:
            self.model_usage[model_name] += 1
            print(f"Incremented usage for {model_name}: {self.model_usage[model_name]}")
        else:
            print(f"Warning: Unknown model {model_name}")
        # Record timestamp for RPM sliding window
        self.rpm_timestamps.setdefault(model_name, []).append(datetime.now())

    async def _log_usage_to_sheet(self, model_name: str, agent_name: str, stage: str, success: bool, latency: float):
        """Append one row to model_usage_log worksheet. Non-blocking:
        failures are logged but never raise or break the pipeline."""
        try:
            from tools.sheet_tool import get_spreadsheet, ensure_worksheet_exists
            headers = ["Timestamp", "Model", "Agent", "Stage", "Status", "Latency (s)"]
            ensure_worksheet_exists("model_usage_log", headers)
            spreadsheet = get_spreadsheet()
            worksheet = spreadsheet.worksheet("model_usage_log")
            worksheet.append_row(
                [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    model_name,
                    agent_name,
                    stage,
                    "success" if success else "error",
                    f"{latency:.1f}",
                ],
                value_input_option="USER_ENTERED",
            )
        except Exception as e:
            logger.warning(f"model_usage_log append failed (non-fatal): {e}")

    def _seed_usage_from_sheet(self):
        """Read today's model_usage_log entries and seed model_usage so
        quota tracking survives across process restarts (each GitHub Actions
        run starts a fresh process). Called once at __init__ time."""
        try:
            from tools.sheet_tool import get_spreadsheet, ensure_worksheet_exists
            headers = ["Timestamp", "Model", "Agent", "Stage", "Status", "Latency (s)"]
            ensure_worksheet_exists("model_usage_log", headers)
            spreadsheet = get_spreadsheet()
            worksheet = spreadsheet.worksheet("model_usage_log")
            records = worksheet.get_all_records()
            today_str = datetime.now().strftime("%Y-%m-%d")
            counts: Dict[str, int] = {}
            for row in records:
                ts = str(row.get("Timestamp", ""))
                model = str(row.get("Model", ""))
                if ts.startswith(today_str) and model in self.model_usage:
                    counts[model] = counts.get(model, 0) + 1
            if counts:
                self.model_usage = {name: counts.get(name, 0) for name in self.model_usage}
                print(f"[Usage] Seeded from sheet: {counts}")
            else:
                print("[Usage] No sheet entries for today; starting fresh.")
        except Exception as e:
            logger.warning(f"model_usage_log seeding failed (non-fatal, starting fresh): {e}")

    def is_llm_error(self, e):
        msg = str(e).lower()
        # 1. Strong typing: known LLM/model exceptions
        if isinstance(e, (ModelBehaviorError, MaxTurnsExceeded)):
            # Exclude tool errors
            if "tool" in msg and not any(x in msg for x in ["model", "rate limit", "quota", "429", "llm", "openai", "gemini", "cohere", "provider"]):
                return False
            return True
        # 2. Exclude user/guardrail errors (assuming these are defined in your code)
        # if isinstance(e, (UserError, InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered)):
        #     return False
        # 3. HTTP/transport errors (rate limit, quota, server error)
        for code in ("429", "500", "503"):
            if hasattr(e, "code") and str(e.code) == code:
                return True
            if hasattr(e, "status") and str(e.status) == code:
                return True
            if code in msg:
                return True
        # 4. Message-based LLM/provider error detection
        for keyword in ["rate limit", "quota", "model", "llm", "openai", "gemini", "cohere", "provider"]:
            if keyword in msg:
                return True
        # 5. Nested/original exception
        orig = getattr(e, "original_exception", None)
        if orig and orig is not e:
            return self.is_llm_error(orig)
        return False

    def is_temporary_error(self, e):
        """Determine if an error is temporary and might be resolved by retrying."""
        msg = str(e).lower()
        # Temporary errors that might be resolved by waiting
        temporary_keywords = ["rate limit", "quota", "429", "503", "timeout", "temporarily unavailable"]
        for keyword in temporary_keywords:
            if keyword in msg:
                return True
        # Check status codes
        for code in ("429", "503"):
            if hasattr(e, "code") and str(e.code) == code:
                return True
            if hasattr(e, "status") and str(e.status) == code:
                return True
        return False

    def is_permanent_error(self, e):
        """Determine if an error is permanent and retrying won't help."""
        msg = str(e).lower()
        # Permanent errors that won't be resolved by retrying
        permanent_keywords = ["invalid api key", "forbidden", "403", "not found", "404"]
        for keyword in permanent_keywords:
            if keyword in msg:
                return True
        # Check status codes
        for code in ("403", "404"):
            if hasattr(e, "code") and str(e.code) == code:
                return True
            if hasattr(e, "status") and str(e.status) == code:
                return True
        return False

    async def run_with_fallback(
        self,
        agent,
        input_data,
        context=None,
        max_turns=15,
        max_retries=2,
        hooks: RunHooks | None = None,
    ) -> RunResult:
        """
        Run an agent with fallback logic across different LLM providers/models.

        If the agent has a ``preferred_model`` attribute, that model is tried
        first (if available) before falling through to the performance-sorted
        free pool.  This enables cross-provider evaluation: a Gemini-written
        post gets evaluated by DeepSeek, and vice versa, without forcing the
        paid model on every agent.

        Usage is logged to the ``model_usage_log`` Google Sheet (non-blocking
        on failure) so quota tracking survives across process restarts.
        """
        last_error = None
        agent_name = getattr(agent, "name", "unknown")
        # Infer stage from agent name (e.g. "Content Generator Agent" -> "content")
        stage = agent_name.split()[0].lower() if agent_name else "unknown"

        for attempt in range(max_retries):
            # Build model list: preferred_model first (if set and available),
            # then performance-sorted free pool, then LAST_RESORT_MODELS.
            sorted_models = self._sort_models_by_performance() + self.LAST_RESORT_MODELS
            preferred_name = getattr(agent, "preferred_model", None)
            if preferred_name:
                preferred_cfg = next(
                    (m for m in self.LLM_MODELS if m["name"] == preferred_name), None
                )
                if preferred_cfg:
                    sorted_models = [preferred_cfg] + [
                        m for m in sorted_models if m["name"] != preferred_name
                    ]

            for model_config in sorted_models:
                try:
                    if not await self.is_model_available(model_config["name"]):
                        continue
                    agent.model = self.get_model_by_name(model_config["name"])
                    print(f"[Fallback] Trying agent '{agent_name}' with model '{model_config['name']}' (attempt {attempt + 1})")
                    if isinstance(input_data, list):
                        print(f"[Debug] Input length for agent '{agent_name}': {len(input_data)}")

                    start_time = datetime.now()
                    result = await self._execute_agent_run(
                        agent,
                        input_data,
                        context=context,
                        max_turns=max_turns,
                        hooks=hooks,
                        session=None,
                    )
                    response_time = (datetime.now() - start_time).total_seconds()
                    await self._update_provider_stats(model_config["name"], True, response_time)
                    await self.increment_usage(model_config["name"])
                    # Log success to sheet (non-blocking)
                    asyncio.create_task(
                        self._log_usage_to_sheet(model_config["name"], agent_name, stage, True, response_time)
                    )
                    print(f"[DEBUG] Agent '{agent_name}' run complete.")
                    return result
                except Exception as e:
                    response_time = (datetime.now() - start_time).total_seconds() if 'start_time' in locals() else 0.0
                    await self._update_provider_stats(model_config["name"], False, response_time)
                    # Log failure to sheet (non-blocking)
                    asyncio.create_task(
                        self._log_usage_to_sheet(model_config["name"], agent_name, stage, False, response_time)
                    )

                    if not self.is_llm_error(e):
                        print(f"[Fallback] Non-LLM error: {e} -- not retrying fallback.")
                        raise

                    print(f"[Fallback] LLM/model error detected: {e} -- retrying fallback.")
                    last_error = e

                    if self.is_permanent_error(e):
                        print(f"[Fallback] Permanent error detected for model {model_config['name']}. Marking as temporarily unavailable.")
                        self.provider_unavailable_until[model_config["name"]] = datetime.now() + timedelta(minutes=5)
                        continue

                    if "rate limit" in str(e).lower() or "429" in str(e):
                        print(f"[Fallback] Rate limit error detected for model {model_config['name']}. Marking as temporarily unavailable.")
                        self.provider_unavailable_until[model_config["name"]] = datetime.now() + timedelta(minutes=10)
                        continue

                    continue

            if attempt < max_retries - 1:
                wait_time = 5 * (2 ** attempt)
                print(f"[Fallback] All models failed for agent '{agent_name}' in attempt {attempt + 1}, waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)

        error_msg = f"All LLM providers failed for agent {agent_name} after {max_retries} retries"
        if last_error:
            error_msg += f". Last error: {str(last_error)}"
        print(f"[Fallback] {error_msg}")
        raise Exception(error_msg)

    def _sort_models_by_performance(self):
        """Sort models by performance (success rate and response time)."""
        def performance_score(model_name):
            stats = self.provider_stats.get(model_name, {"success_count": 0, "error_count": 0, "avg_response_time": 0.0})
            total_requests = stats["success_count"] + stats["error_count"]
            if total_requests == 0:
                # No data, return neutral score
                return 0
            success_rate = stats["success_count"] / total_requests
            # Lower response time is better, so we invert it (higher score is better)
            # We use 1.0 as a base to avoid negative scores
            response_time_score = 1.0 / (1.0 + stats["avg_response_time"])
            # Weighted score: 70% success rate, 30% response time
            return 0.7 * success_rate + 0.3 * response_time_score

        # Sort models by performance score (descending)
        return sorted(self.LLM_MODELS, key=lambda m: performance_score(m["name"]), reverse=True)

    async def _update_provider_stats(self, model_name, success, response_time):
        """Update per-model statistics for performance tracking."""
        if model_name not in self.provider_stats:
            return

        stats = self.provider_stats[model_name]
        total_requests = stats["success_count"] + stats["error_count"]
        
        if success:
            stats["success_count"] += 1
        else:
            stats["error_count"] += 1
            
        # Update average response time
        if total_requests == 0:
            stats["avg_response_time"] = response_time
        else:
            # Running average
            stats["avg_response_time"] = (stats["avg_response_time"] * total_requests + response_time) / (total_requests + 1)
            
        print(f"[Stats] Model {model_name}: Success rate={(stats['success_count']/(stats['success_count']+stats['error_count'])):.2f}, Avg response time={stats['avg_response_time']:.2f}s")

    async def _execute_agent_run(self, agent, input_data, context=None, max_turns=15, hooks=None, session=None):
        """Helper wrapper that actually invokes the parent AgentRunner.run.

        This exists so tests or fake runners can override this single method
        to simulate agent behavior without requiring a full Agents SDK.
        """
        # In normal operation, delegate to the parent class implementation.
        return await super().run(agent, input_data, context=context, max_turns=max_turns, hooks=hooks, session=session)