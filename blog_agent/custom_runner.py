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
from datetime import datetime, timedelta

# Custom runner that extends AgentRunner and adds fallback logic
class FallbackAgentRunner(AgentRunner):
    def __init__(self):
        super().__init__()
        # LLM model configurations
        # Gemini models share a single quota; other providers have their own
        # Preferred ordering: minimax first so it's chosen initially when no
        # provider statistics exist. The fallback logic will still reorder
        # models by performance over time.
        self.LLM_MODELS = [
            # {"name": "minimax-m2", "model": "MiniMax-M2", "client": self.get_minimax_client, "provider": "minimax"},
            {"name": "gemini-2.5-flash", "model": "gemini-2.5-flash", "provider": "gemini"},
            {"name": "gemini-2.5-flash-lite", "model": "gemini-2.5-flash-lite", "provider": "gemini"},
            {"name": "cohere", "model": "command-a-03-2025", "client": self.get_cohere_client, "provider": "cohere"},
            # openrouter/free is OpenRouter's own auto-router: it always
            # resolves to whatever free model is currently available instead
            # of a hardcoded :free model ID. OpenRouter's free catalog churns
            # fast (one tracker recorded a third of it delisted in 9 days) --
            # this was added specifically to replace a hardcoded qwen model
            # that got fully removed from OpenRouter, breaking every call.
            {"name": "openrouter-free", "model": "openrouter/free", "provider": "openrouter"},
        ]

        # Quota tracking at provider level
        self.model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0,
        # "minimax": 0
        }
        # Approximate daily limits. openrouter's free tier is 50/day without
        # ever having purchased credits (1000/day only applies once you've
        # bought $10+ in credits at some point, which isn't "free" anymore).
        self.model_limits = {"gemini": 50, "openrouter": 50, "cohere": 33, "minimax": 500}
        self.last_reset = datetime.now()

        # Provider performance tracking
        self.provider_stats = {
            "gemini": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0},
            "openrouter": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0},
            "cohere": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0},
            # "minimax": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0}
        }

        # Temporary provider unavailability tracking
        self.provider_unavailable_until = {"gemini": None, "openrouter": None, "cohere": None, "minimax": None}

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

    def get_cohere_client(self):
        from agents import AsyncOpenAI
        return AsyncOpenAI(
            base_url="https://api.cohere.ai/compatibility/v1",
            api_key=os.environ['COHERE_API_KEY']
        )

    def get_minimax_client(self):
        from agents import AsyncOpenAI
        # MiniMax-compatible API client using the same AsyncOpenAI wrapper
        return AsyncOpenAI(
            base_url="https://api.minimax.io/v1",
            api_key=os.environ['MINIMAX_API_KEY']
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
                    elif model_config["provider"] == "openrouter":
                        client = self.get_openrouter_client()
                    elif model_config["provider"] == "cohere":
                        client = self.get_cohere_client()
                    # elif model_config["provider"] == "minimax":
                    #     client = self.get_minimax_client()
                    else:
                        raise ValueError(f"Unknown provider: {model_config['provider']}")
                
                return OpenAIChatCompletionsModel(
                    model=model_config["model"],
                    openai_client=client
                )
        
        # If not found, raise error instead of defaulting
        available_models = [m["name"] for m in self.LLM_MODELS]
        raise ValueError(f"Model name '{model_name}' not found in LLM_MODELS. Available models: {available_models}")

    async def is_model_available(self, provider_name):
        """Check if the provider has quota remaining and is not temporarily unavailable."""
        # Reset usage daily
        if (datetime.now() - self.last_reset).days >= 1:
            print(f"Resetting daily usage counters for all providers")
            self.model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0, 
            # "minimax": 0
            }
            self.last_reset = datetime.now()
        
        # Check if provider is temporarily unavailable
        if self.provider_unavailable_until[provider_name] is not None:
            if datetime.now() < self.provider_unavailable_until[provider_name]:
                print(f"Provider {provider_name} is temporarily unavailable")
                return False
            else:
                # Reset unavailability
                self.provider_unavailable_until[provider_name] = None
        
        current_usage = self.model_usage.get(provider_name, 0)
        limit = self.model_limits.get(provider_name, 0)
        available = current_usage < limit - 5
        
        print(f"Provider {provider_name}: {current_usage}/{limit} calls used, available: {available}")
        return available

    async def increment_usage(self, provider_name):
        """Increment usage for the provider."""
        if provider_name in self.model_usage:
            self.model_usage[provider_name] += 1
            print(f"Incremented usage for {provider_name}: {self.model_usage[provider_name]}")
        else:
            print(f"Warning: Unknown provider {provider_name}")

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
        max_retries=3,
        hooks: RunHooks | None = None,
    ) -> RunResult:
        """
        Run an agent with fallback logic across different LLM providers/models.

        Note: this does not chase SDK-level handoffs itself. `Runner.run()`
        (invoked via `_execute_agent_run`) already resolves any handoffs
        configured on an agent within a single call, so by the time a result
        comes back, `result.last_agent` is just informational, not a signal
        that execution stopped mid-handoff.
        """
        last_error = None

        for attempt in range(max_retries):
            sorted_models = self._sort_models_by_performance()

            for model_config in sorted_models:
                try:
                    if not await self.is_model_available(model_config["provider"]):
                        continue
                    agent.model = self.get_model_by_name(model_config["name"])
                    print(f"[Fallback] Trying agent '{agent.name}' with model '{model_config['name']}' (attempt {attempt + 1})")
                    if isinstance(input_data, list):
                        print(f"[Debug] Input length for agent '{agent.name}': {len(input_data)}")

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
                    await self._update_provider_stats(model_config["provider"], True, response_time)
                    await self.increment_usage(model_config["provider"])
                    print(f"[DEBUG] Agent '{agent.name}' run complete.")
                    return result
                except Exception as e:
                    response_time = (datetime.now() - start_time).total_seconds() if 'start_time' in locals() else 0.0
                    await self._update_provider_stats(model_config["provider"], False, response_time)

                    if not self.is_llm_error(e):
                        print(f"[Fallback] Non-LLM error: {e} -- not retrying fallback.")
                        raise

                    print(f"[Fallback] LLM/model error detected: {e} -- retrying fallback.")
                    last_error = e

                    if self.is_permanent_error(e):
                        print(f"[Fallback] Permanent error detected for provider {model_config['provider']}. Marking as temporarily unavailable.")
                        self.provider_unavailable_until[model_config["provider"]] = datetime.now() + timedelta(minutes=5)
                        continue

                    if "rate limit" in str(e).lower() or "429" in str(e):
                        print(f"[Fallback] Rate limit error detected for provider {model_config['provider']}. Marking as temporarily unavailable.")
                        self.provider_unavailable_until[model_config["provider"]] = datetime.now() + timedelta(minutes=10)
                        continue

                    continue

            if attempt < max_retries - 1:
                wait_time = 5 * (2 ** attempt)
                print(f"[Fallback] All models failed for agent '{agent.name}' in attempt {attempt + 1}, waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)

        error_msg = f"All LLM providers failed for agent {agent.name} after {max_retries} retries"
        if last_error:
            error_msg += f". Last error: {str(last_error)}"
        print(f"[Fallback] {error_msg}")
        raise Exception(error_msg)

    def _sort_models_by_performance(self):
        """Sort models by performance (success rate and response time)."""
        def performance_score(provider_name):
            stats = self.provider_stats.get(provider_name, {"success_count": 0, "error_count": 0, "avg_response_time": 0.0})
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
        return sorted(self.LLM_MODELS, key=lambda m: performance_score(m["provider"]), reverse=True)

    async def _update_provider_stats(self, provider_name, success, response_time):
        """Update provider statistics for performance tracking."""
        if provider_name not in self.provider_stats:
            return
            
        stats = self.provider_stats[provider_name]
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
            
        print(f"[Stats] Provider {provider_name}: Success rate={(stats['success_count']/(stats['success_count']+stats['error_count'])):.2f}, Avg response time={stats['avg_response_time']:.2f}s")

    async def _execute_agent_run(self, agent, input_data, context=None, max_turns=15, hooks=None, session=None):
        """Helper wrapper that actually invokes the parent AgentRunner.run.

        This exists so tests or fake runners can override this single method
        to simulate agent behavior without requiring a full Agents SDK.
        """
        # In normal operation, delegate to the parent class implementation.
        return await super().run(agent, input_data, context=context, max_turns=max_turns, hooks=hooks, session=session)