# test_posting_fix.py
import asyncio
import logging
from blog_agent.posting_agent import run_posting_workflow

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_posting_fix():
    """Test that the posting workflow now correctly calls the post_to_sanity_tool."""
    logger.info("Testing the updated posting workflow...")
    
    try:
        result = await run_posting_workflow()
        logger.info(f"Posting workflow result: {result}")
        
        if result.get("status") == "no_posts_found":
            logger.info("No approved, unpublished posts found in the worksheet.")
        elif result.get("status") == "completed":
            logger.info("Posting workflow completed.")
        else:
            logger.info(f"Posting workflow finished with status: {result.get('status')}")
            
    except Exception as e:
        logger.error(f"Error in posting workflow: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(test_posting_fix())