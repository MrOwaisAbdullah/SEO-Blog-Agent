import asyncio
from blog_agents import triage_agent
from llm_clients import run_flow_with_agent_fallback, LLM_MODELS, is_model_available, get_model_by_name, increment_usage


async def test_agent_workflow():
    """Tests the full AgentWorkflow."""
    # Initialize and run the workflow
    result = await run_flow_with_agent_fallback(
        triage_agent,
        "Generate a new blog post",
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_retries=3,
        max_turns=30
    )
    print("AgentWorkflow Result:", result)

if __name__ == "__main__":
    asyncio.run(test_agent_workflow())