from agents import RunResult, RunHooks
from agents.models.interface import Model
from agents.model_settings import ModelSettings
from agents.exceptions import ModelBehaviorError, MaxTurnsExceeded
from typing import Any, Dict, List, Optional, Union
import asyncio
import copy
import os
from datetime import datetime
from agents.run import AgentRunner

# Custom runner that extends AgentRunner and adds fallback logic
class FallbackAgentRunner(AgentRunner):
    def __init__(self):
        super().__init__()
        # LLM model configurations
        # Gemini models share a single quota; other providers have their own
        self.LLM_MODELS = [
            {"name": "gemini-2.5-flash", "model": "gemini-2.5-flash", "provider": "gemini"},
            {"name": "kimi-openrouter", "model": "moonshotai/kimi-k2:free", "provider": "openrouter"},
            {"name": "gemini-2.5-flash-lite", "model": "gemini-2.5-flash-lite", "provider": "gemini"},
            {"name": "cohere", "model": "command-a-03-2025", "client": self.get_cohere_client, "provider": "cohere"},
            {"name": "gemini-2.0-flash", "model": "gemini-2.0-flash", "provider": "gemini"},
        ]
        
        # Quota tracking at provider level
        self.model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0}
        self.model_limits = {"gemini": 50, "openrouter": 1000, "cohere": 33}  # Approximate daily limits
        self.last_reset = datetime.now()

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
        """Check if the provider has quota remaining (buffer of 5 calls)."""
        # Reset usage daily
        if (datetime.now() - self.last_reset).days >= 1:
            print(f"Resetting daily usage counters for all providers")
            self.model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0}
            self.last_reset = datetime.now()
        
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

        while True:
            print(f"[DEBUG] Entering fallback loop for agent: {getattr(current_agent, 'name', str(current_agent))}")
            print(f"[DEBUG] Current input: {current_input}")
            print(f"[DEBUG] Current session: {session}")
            
            for attempt in range(max_retries):
                for model_config in self.LLM_MODELS:
                    try:
                        if not await self.is_model_available(model_config["provider"]):
                            continue
                        current_agent.model = self.get_model_by_name(model_config["name"])
                        print(f"[Fallback] Trying agent '{current_agent.name}' with model '{model_config['name']}' (attempt {attempt+1})")
                        if isinstance(current_input, list):
                            print(f"[Debug] Input length for agent '{current_agent.name}': {len(current_input)}")
                        
                        # Use the parent class's run method
                        result = await super().run(
                            current_agent, 
                            current_input, 
                            context=context,
                            max_turns=max_turns,
                            hooks=hooks,
                            session=session
                        )
                        
                        await self.increment_usage(model_config["provider"])
                        print(f"[DEBUG] Agent '{current_agent.name}' run complete. Result: {result}")
                        
                        # Handle handoffs properly
                        if hasattr(result, '_last_agent') and result._last_agent != current_agent:
                            print(f"[DEBUG] Handoff detected. Last agent: {getattr(result._last_agent, 'name', str(result._last_agent))}")
                            current_agent = result._last_agent
                            current_input = result.input
                            session = getattr(result, "session", None) if hasattr(result, "session") else None
                            print(f"[Fallback] Handoff to agent '{current_agent.name}' with input: {current_input} and session: {session}")
                            # After handoff, restart the while True loop with the new agent/input/session
                            break  # break out of model loop, continue with new agent
                        else:
                            print(f"[DEBUG] No handoff. Returning result for agent '{current_agent.name}'")
                            return result
                    except Exception as e:
                        if self.is_llm_error(e):
                            print(f"[Fallback] LLM/model error detected: {e} -- retrying fallback.")
                            last_error = e
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
                # If we broke out of the model loop due to handoff, restart the while True loop
                break
            else:
                error_msg = f"All LLM providers failed for agent {current_agent.name} after {max_retries} retries"
                if last_error:
                    error_msg += f". Last error: {str(last_error)}"
                print(f"[Fallback] {error_msg}")
                raise Exception(error_msg)