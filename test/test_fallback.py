import asyncio
from blog_agents import triage_agent
from llm_clients import get_available_models
from llm_clients import run_flow_with_agent_fallback

async def test_fallback_logic():
    """Test the improved fallback logic."""
    print("=== Testing Fallback Logic ===")
    
    # Check available models
    available_models = await get_available_models()
    print(f"Available models: {available_models}")
    
    # Test running triage agent with fallback
    print("\n=== Testing Triage Agent with Fallback ===")
    try:
        result = await run_flow_with_agent_fallback(
            agent=triage_agent,
            input_data="Generate a post",
            max_retries=2
        )
        
        if "error" in result:
            print(f"❌ Fallback failed: {result['error']}")
        else:
            print(f"✅ Fallback succeeded: {result}")
            
    except Exception as e:
        print(f"❌ Exception occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_fallback_logic()) 