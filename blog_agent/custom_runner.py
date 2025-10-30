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
import hashlib
import asyncio
import os
from datetime import datetime, timedelta

# Custom runner that extends AgentRunner and adds fallback logic
class FallbackAgentRunner(AgentRunner):
    def __init__(self):
        super().__init__()
        # LLM model configurations
        # Gemini models share a single quota; other providers have their own
        self.LLM_MODELS = [
            {"name": "gemini-2.5-flash", "model": "gemini-2.5-flash", "provider": "gemini"},
            {"name": "qwen-2.5-openrouter", "model": "qwen/qwen2.5-vl-32b-instruct:free", "provider": "openrouter"},
            {"name": "gemini-2.5-flash-lite", "model": "gemini-2.5-flash-lite", "provider": "gemini"},
            {"name": "cohere", "model": "command-a-03-2025", "client": self.get_cohere_client, "provider": "cohere"},
            {"name": "gemini-2.0-flash", "model": "gemini-2.0-flash", "provider": "gemini"},
        ]
        
        # Quota tracking at provider level
        self.model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0}
        self.model_limits = {"gemini": 50, "openrouter": 1000, "cohere": 33}  # Approximate daily limits
        self.last_reset = datetime.now()
        
        # Provider performance tracking
        self.provider_stats = {
            "gemini": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0},
            "openrouter": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0},
            "cohere": {"success_count": 0, "error_count": 0, "avg_response_time": 0.0}
        }
        
        # Temporary provider unavailability tracking
        self.provider_unavailable_until = {"gemini": None, "openrouter": None, "cohere": None}

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
            self.model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0}
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
        Run an agent with fallback logic across different models.
        """
        current_agent = agent
        current_input = input_data
        session = None
        last_error = None

        # Track agents we've executed in this run to avoid duplicate handoffs
        # Use object id() for robustness in case multiple agents share the same name
        seen_agent_ids = set()
        # Track how many times an agent (by id) was invoked in this run
        agent_invocation_counts: Dict[int, int] = {}
        # Track processed handoffs by (agent_name, input_hash) to avoid duplicate processing
        processed_handoffs = set()

        # Add a maximum iteration limit to prevent infinite handoff loops
        max_iterations = 10
        iteration_count = 0

        while iteration_count < max_iterations:
            iteration_count += 1
            # Flag to indicate a handoff occurred during this iteration so we can
            # restart the outer loop and continue with the new agent/input while
            # preserving tracking state (seen_agent_ids, processed_handoffs, etc.).
            handoff_occurred = False
            print(f"[DEBUG] Entering fallback loop for agent: {getattr(current_agent, 'name', str(current_agent))}")
            print(f"[DEBUG] Current input: {current_input}")
            print(f"[DEBUG] Current session: {session}")
            print(f"[DEBUG] Iteration count: {iteration_count}/{max_iterations}")
            
            # Sort models by performance (success rate) for this run
            sorted_models = self._sort_models_by_performance()
            
            for attempt in range(max_retries):
                # Try models in performance-sorted order
                for model_config in sorted_models:
                    try:
                        if not await self.is_model_available(model_config["provider"]):
                            continue
                        current_agent.model = self.get_model_by_name(model_config["name"])
                        print(f"[Fallback] Trying agent '{current_agent.name}' with model '{model_config['name']}' (attempt {attempt+1})")
                        if isinstance(current_input, list):
                            print(f"[Debug] Input length for agent '{current_agent.name}': {len(current_input)}")
                        
                        # Time the request for performance tracking
                        start_time = datetime.now()
                        
                        # Use the parent class's run method (extracted to a helper so
                        # tests/fakes can override _execute_agent_run without requiring
                        # a full AgentRunner implementation)
                        result = await self._execute_agent_run(
                            current_agent,
                            current_input,
                            context=context,
                            max_turns=max_turns,
                            hooks=hooks,
                            session=session,
                        )
                        
                        # Calculate response time and update stats
                        response_time = (datetime.now() - start_time).total_seconds()
                        await self._update_provider_stats(model_config["provider"], True, response_time)
                        
                        await self.increment_usage(model_config["provider"])
                        print(f"[DEBUG] Agent '{current_agent.name}' run complete. Result: {result}")

                        # Mark this agent as executed in this run (by object id)
                        try:
                            current_agent_id = id(current_agent)
                        except Exception:
                            current_agent_id = id(current_agent)
                        seen_agent_ids.add(current_agent_id)
                        agent_invocation_counts[current_agent_id] = agent_invocation_counts.get(current_agent_id, 0) + 1

                        # Handle handoffs properly
                        # The SDK may expose the handoff target as either `last_agent`
                        # (property) or `_last_agent` (internal). Prefer the public
                        # `last_agent` attribute but fall back to `_last_agent`.
                        has_last_agent_prop = hasattr(result, 'last_agent')
                        has__last_agent_attr = hasattr(result, '_last_agent')
                        print(f"[DEBUG] result has last_agent prop: {has_last_agent_prop}, has _last_agent attr: {has__last_agent_attr}")

                        last_agent_obj = None
                        if has_last_agent_prop:
                            try:
                                last_agent_obj = result.last_agent
                            except Exception:
                                last_agent_obj = getattr(result, 'last_agent', None)
                        elif has__last_agent_attr:
                            last_agent_obj = result._last_agent

                        if last_agent_obj is not None:
                            try:
                                last_agent_name = getattr(last_agent_obj, 'name', str(last_agent_obj))
                            except Exception:
                                last_agent_name = str(last_agent_obj)
                            print(f"[DEBUG] detected last_agent: {last_agent_name}")
                            print(f"[DEBUG] current_agent name: {getattr(current_agent, 'name', str(current_agent))}")
                            print(f"[DEBUG] current_agent id: {id(current_agent)}, last_agent id: {id(last_agent_obj)}")
                            print(f"[DEBUG] seen_agent_ids before handoff check: {seen_agent_ids}")

                        # Only consider a handoff if there is a last_agent and it's a different agent
                        if last_agent_obj and last_agent_obj != current_agent:
                            handoff_agent = last_agent_obj
                            handoff_agent_name = getattr(handoff_agent, 'name', str(handoff_agent))
                            handoff_agent_id = id(handoff_agent)
                            print(f"[DEBUG] Handoff detected. Last agent: {handoff_agent_name} (id={handoff_agent_id})")

                            # If we've already executed this agent in the current run (by id), avoid re-invoking it
                            if handoff_agent_id in seen_agent_ids:
                                print(f"[Fallback] Handoff to agent '{handoff_agent_name}' ignored because it was already executed in this run (by id). Returning result to avoid duplicate runs.")
                                return result

                            # Determine handoff input from possible result fields (input, final_output, output)
                            handoff_input = None
                            if hasattr(result, 'input'):
                                handoff_input = result.input
                            if not handoff_input and hasattr(result, 'final_output'):
                                handoff_input = getattr(result, 'final_output')
                            if not handoff_input and hasattr(result, 'output'):
                                handoff_input = getattr(result, 'output')

                            # If the handoff doesn't include a valid input, ignore it to avoid running an agent with empty input
                            if not handoff_input or (isinstance(handoff_input, str) and not handoff_input.strip()):
                                print(f"[Fallback] Handoff to agent '{handoff_agent_name}' ignored because handoff input is empty or missing.")
                                return result

                            # Compute a stable key for this handoff (agent name + hash of normalized input)
                            try:
                                normalized_input = (str(handoff_input).strip() if handoff_input is not None else "")
                                input_hash = hashlib.sha256(normalized_input.encode('utf-8')).hexdigest()
                                handoff_key = (handoff_agent_name, input_hash)
                            except Exception:
                                handoff_key = (handoff_agent_name, str(handoff_input))

                            if handoff_key in processed_handoffs:
                                print(f"[Fallback] Handoff to agent '{handoff_agent_name}' with the same input was already processed in this run; skipping to avoid duplicate invocation.")
                                return result

                            # If the result already appears to contain inserted images (markdown),
                            # assume the handoff was already applied (by the SDK or agent) and skip re-invocation.
                            existing_output_text = None
                            if hasattr(result, 'final_output') and result.final_output:
                                existing_output_text = result.final_output
                            elif hasattr(result, 'output') and result.output:
                                existing_output_text = result.output
                            elif hasattr(result, 'text') and result.text:
                                existing_output_text = result.text

                            if isinstance(existing_output_text, str):
                                # simple heuristic: detect markdown image syntax
                                import re
                                if re.search(r"!\[.*\]\(.*\)", existing_output_text):
                                    print(f"[Fallback] Detected image markdown in result output; assuming handoff already applied. Skipping explicit handoff to '{handoff_agent_name}' and returning result.")
                                    return result

                            # Otherwise, proceed with the handoff. Do NOT mark the
                            # handoff agent as seen yet — we'll mark it once it actually
                            # executes in the next outer loop iteration. Marking it
                            # early can cause timing issues where the agent is skipped
                            # incorrectly or allowed to run twice depending on how the
                            # SDK manages handoffs internally.
                            agent_invocation_counts[handoff_agent_id] = agent_invocation_counts.get(handoff_agent_id, 0)
                            # Mark this handoff as processed
                            try:
                                # Use agent id in the handoff key for robustness
                                if isinstance(handoff_key, tuple) and handoff_key[0] != handoff_agent_id:
                                    handoff_key = (handoff_agent_id, handoff_key[1] if len(handoff_key) > 1 else handoff_key[1])
                                processed_handoffs.add(handoff_key)
                            except Exception:
                                pass
                            # Update current agent/input/session and mark that a handoff occurred.
                            # We do NOT recursively call run_with_fallback because that would
                            # reset our tracking state (seen_agent_ids / processed_handoffs) and
                            # allow duplicate invocations. Instead, break out of the model loop
                            # so the outer while loop restarts with the new agent/input and
                            # the existing tracking state preserved.
                            current_agent = handoff_agent
                            current_input = handoff_input
                            session = getattr(result, "session", None) if hasattr(result, "session") else None
                            print(f"[Fallback] Handoff to agent '{getattr(current_agent, 'name', str(current_agent))}' (id={handoff_agent_id}) with input: {current_input} and session: {session}")
                            handoff_occurred = True
                            # Break out of the current model/config loop so the outer while
                            # iteration will continue with the handoff agent using the
                            # same tracking sets (seen_agent_ids, processed_handoffs).
                            break
                        else:
                            print(f"[DEBUG] No handoff. Returning result for agent '{current_agent.name}'")
                            return result
                    except Exception as e:
                        # Calculate response time even for failed requests
                        response_time = (datetime.now() - start_time).total_seconds() if 'start_time' in locals() else 0.0
                        await self._update_provider_stats(model_config["provider"], False, response_time)
                        
                        if self.is_llm_error(e):
                            print(f"[Fallback] LLM/model error detected: {e} -- retrying fallback.")
                            last_error = e
                            
                            # If it's a permanent error, don't retry with the same provider
                            if self.is_permanent_error(e):
                                print(f"[Fallback] Permanent error detected for provider {model_config['provider']}. Marking as temporarily unavailable.")
                                # Mark provider as temporarily unavailable for 5 minutes
                                self.provider_unavailable_until[model_config["provider"]] = datetime.now() + timedelta(minutes=5)
                                continue  # Skip to next provider
                            
                            # If it's a rate limit error, mark as temporarily unavailable for longer
                            if "rate limit" in str(e).lower() or "429" in str(e):
                                print(f"[Fallback] Rate limit error detected for provider {model_config['provider']}. Marking as temporarily unavailable.")
                                # Mark provider as temporarily unavailable for 10 minutes
                                self.provider_unavailable_until[model_config["provider"]] = datetime.now() + timedelta(minutes=10)
                                continue  # Skip to next provider
                            
                            continue  # fallback logic continues
                        else:
                            print(f"[Fallback] Non-LLM error: {e} -- not retrying fallback.")
                            raise
                else:
                    if attempt < max_retries - 1:
                        wait_time = 5 * (2 ** attempt)
                        print(f"[Fallback] All models failed for agent '{current_agent.name}' in attempt {attempt+1}, waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                    else:
                        error_msg = f"All LLM providers failed for agent {current_agent.name} after {max_retries} retries"
                        if last_error:
                            error_msg += f". Last error: {str(last_error)}"
                        print(f"[Fallback] {error_msg}")
                        raise Exception(error_msg)
                # If a handoff occurred above we broke out of the model loop; restart
                # the outer while loop so the handoff agent is executed in the same
                # run with preserved tracking state.
                if handoff_occurred:
                    # continue the outer while loop (it will increment iteration_count and retry)
                    break
                # If we broke out of the model loop for other reasons, also break to
                # restart the outer loop.
                break
            else:
                error_msg = f"All LLM providers failed for agent {current_agent.name} after {max_retries} retries"
                if last_error:
                    error_msg += f". Last error: {str(last_error)}"
                print(f"[Fallback] {error_msg}")
                raise Exception(error_msg)
        
        # If we've reached the maximum iterations, raise an error
        error_msg = f"Maximum iterations ({max_iterations}) reached for agent {getattr(current_agent, 'name', str(current_agent))}. Possible infinite handoff loop detected."
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