from agents import AsyncOpenAI, OpenAIChatCompletionsModel, set_tracing_disabled
import os
from datetime import datetime
import asyncio
from dotenv import load_dotenv
from agents.exceptions import ModelBehaviorError, MaxTurnsExceeded, UserError, InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered
import copy
load_dotenv()
from agents import set_tracing_export_api_key

tracing_api_key = os.environ["OPENAI_API_KEY"]
set_tracing_export_api_key(tracing_api_key)

# Disable tracing to avoid OpenAI API key requirement
# set_tracing_disabled(True)

# Custom OpenAI clients
def get_gemini_client():
    return AsyncOpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.environ['GEMINI_API_KEY']
    )

def get_openrouter_client():
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ['OPENROUTER_API_KEY']
    )

def get_cohere_client():
    return AsyncOpenAI(
        base_url="https://api.cohere.ai/compatibility/v1",
        api_key=os.environ['COHERE_API_KEY']
    )

# LLM model configurations
# Gemini models share a single quota; other providers have their own
LLM_MODELS = [
    {"name": "gemini-2.5-flash", "model": "gemini-2.5-flash", "client": get_gemini_client(), "provider": "gemini"},
    {"name": "kimi-openrouter", "model": "moonshotai/kimi-k2:free", "client": get_openrouter_client(), "provider": "openrouter"},
    {"name": "gemini-2.5-flash-lite", "model": "gemini-2.5-flash-lite", "client": get_gemini_client(), "provider": "gemini"},
    {"name": "cohere", "model": "command-a-03-2025", "client": get_cohere_client(), "provider": "cohere"},
    {"name": "gemini-2.0-flash", "model": "gemini-2.0-flash", "client": get_gemini_client(), "provider": "gemini"},
]

# Quota tracking at provider level
model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0}
model_limits = {"gemini": 50, "openrouter": 1000, "cohere": 33}  # Approximate daily limits
last_reset = datetime.now()

async def is_model_available(provider_name):
    """Check if the provider has quota remaining (buffer of 5 calls)."""
    global last_reset, model_usage
    
    # Reset usage daily
    if (datetime.now() - last_reset).days >= 1:
        print(f"Resetting daily usage counters for all providers")
        model_usage = {"gemini": 0, "openrouter": 0, "cohere": 0}
        last_reset = datetime.now()
    
    current_usage = model_usage.get(provider_name, 0)
    limit = model_limits.get(provider_name, 0)
    available = current_usage < limit - 5
    
    print(f"Provider {provider_name}: {current_usage}/{limit} calls used, available: {available}")
    return available

async def increment_usage(provider_name):
    """Increment usage for the provider."""
    if provider_name in model_usage:
        model_usage[provider_name] += 1
        print(f"Incremented usage for {provider_name}: {model_usage[provider_name]}")
    else:
        print(f"Warning: Unknown provider {provider_name}")

async def get_available_models():
    """Get list of currently available models."""
    available = []
    for model_config in LLM_MODELS:
        if await is_model_available(model_config["provider"]):
            available.append(model_config["name"])
    return available

def get_model_by_name(model_name):
    """Returns the model configuration for the specified model name."""
    for model_config in LLM_MODELS:
        if model_config["name"] == model_name:
            # Validate model name is not empty
            if not model_config["model"] or model_config["model"].strip() == "":
                raise ValueError(f"Model config for {model_name} has empty model string!")
            
            print(f"Creating model: {model_name} -> {model_config['model']}")
            return OpenAIChatCompletionsModel(
                model=model_config["model"],
                openai_client=model_config["client"]
            )
    
    # If not found, raise error instead of defaulting
    available_models = [m["name"] for m in LLM_MODELS]
    raise ValueError(f"Model name '{model_name}' not found in LLM_MODELS. Available models: {available_models}")

def is_llm_error(e):
    msg = str(e).lower()
    # 1. Strong typing: known LLM/model exceptions
    if isinstance(e, (ModelBehaviorError, MaxTurnsExceeded)):
        # Exclude tool errors
        if "tool" in msg and not any(x in msg for x in ["model", "rate limit", "quota", "429", "llm", "openai", "gemini", "cohere", "provider"]):
            return False
        return True
    # 2. Exclude user/guardrail errors
    if isinstance(e, (UserError, InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered)):
        return False
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
        return is_llm_error(orig)
    return False

async def run_flow_with_agent_fallback(agent, input_data, LLM_MODELS, is_model_available, get_model_by_name, increment_usage, max_retries=3, max_turns=15):
    current_agent = agent
    current_input = input_data
    session = None
    last_error = None

    while True:
        print(f"[DEBUG] Entering fallback loop for agent: {getattr(current_agent, 'name', str(current_agent))}")
        print(f"[DEBUG] Current input: {current_input}")
        print(f"[DEBUG] Current session: {session}")
        for attempt in range(max_retries):
            for model_config in LLM_MODELS:
                try:
                    if not await is_model_available(model_config["provider"]):
                        continue
                    current_agent.model = get_model_by_name(model_config["name"])
                    print(f"[Fallback] Trying agent '{current_agent.name}' with model '{model_config['name']}' (attempt {attempt+1})")
                    if isinstance(current_input, list):
                        print(f"[Debug] Input length for agent '{current_agent.name}': {len(current_input)}")
                    from agents import Runner  # local import to avoid circular
                    result = await Runner.run(current_agent, current_input, session=session, max_turns=max_turns)
                    await increment_usage(model_config["provider"])
                    print(f"[DEBUG] Agent '{current_agent.name}' run complete. Result: {result}")
                    if hasattr(result, "handoff") and result.handoff:
                        print(f"[DEBUG] Handoff detected. Handoff object: {result.handoff}")
                        print(f"[DEBUG] Handoff agent: {getattr(result.handoff.agent, 'name', str(result.handoff.agent))}")
                        current_agent = result.handoff.agent
                        current_input = result.to_input_list() if hasattr(result, "to_input_list") else copy.deepcopy(current_input)
                        session = getattr(result, "session", None)
                        print(f"[Fallback] Handoff to agent '{current_agent.name}' with input: {current_input} and session: {session}")
                        # After handoff, restart the while True loop with the new agent/input/session
                        break  # break out of model loop, continue with new agent
                    else:
                        print(f"[DEBUG] No handoff. Returning result for agent '{current_agent.name}'")
                        return result.final_output
                except Exception as e:
                    if is_llm_error(e):
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
                    import asyncio
                    await asyncio.sleep(wait_time)
                else:
                    error_msg = f"All LLM providers failed for agent {current_agent.name} after {max_retries} retries"
                    if last_error:
                        error_msg += f". Last error: {str(last_error)}"
                    print(f"[Fallback] {error_msg}")
                    return {"error": error_msg}
            # If we broke out of the model loop due to handoff, restart the while True loop
            break
        else:
            error_msg = f"All LLM providers failed for agent {current_agent.name} after {max_retries} retries"
            if last_error:
                error_msg += f". Last error: {str(last_error)}"
            print(f"[Fallback] {error_msg}")
            return {"error": error_msg}