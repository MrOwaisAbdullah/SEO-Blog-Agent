# verify_fixes.py
import asyncio
import logging
from blog_agent.posting_agent import preparation_agent
from blog_agent.llm_clients import (
    LLM_MODELS,
    is_model_available,
    get_model_by_name,
    increment_usage
)
from blog_agent.posting_agent import run_flow_with_agent_fallback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_preparation_agent():
    """Test that the preparation agent can find approved but unpublished posts."""
    logger.info("Testing Preparation Agent...")
    
    try:
        result = await run_flow_with_agent_fallback(
            preparation_agent,
            "Prepare the next blog post for publishing.",
            LLM_MODELS,
            is_model_available,
            get_model_by_name,
            increment_usage,
            max_retries=1,
            max_turns=10
        )
        
        logger.info(f"Preparation Agent result: {result}")
        
        # Check if we got a string result
        if isinstance(result, str):
            if "NO_POSTS_FOUND" in result:
                logger.info("No approved, unpublished posts found.")
            else:
                logger.info("Preparation Agent found a post to publish.")
                logger.info(f"Post data preview: {result[:200]}...")
        else:
            logger.info(f"Preparation Agent returned non-string result: {type(result)}")
            
    except Exception as e:
        logger.error(f"Error in Preparation Agent test: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(test_preparation_agent())