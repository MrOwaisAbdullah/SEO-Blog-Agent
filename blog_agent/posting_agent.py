# blog_agent/posting_agent.py

import logging
from agents import Agent, ModelSettings, AgentHooks, handoff
from tools.tools import post_to_sanity_tool, get_stock_image_tool, fetch_internal_links_tool # Ensure correct import paths
from tools.sheet_tool import manage_sheet_data_tool # Ensure correct import path
from typing import Dict, Any, List, Optional
import json
import asyncio
import copy

# Import your runner and model functions
# Adjust these imports based on your project structure
from blog_agent.llm_clients import (
    run_flow_with_agent_fallback,
    LLM_MODELS,
    is_model_available,
    get_model_by_name,
    increment_usage
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Hooks (Optional, can be shared or specific) ---
class MyAgentHooks(AgentHooks):
    async def on_agent_start(self, context, agent):
        logger.info(f"[Hook] Agent start: {agent.name}")

    async def on_agent_end(self, context, agent, result):
        logger.info(f"[Hook] Agent end: {agent.name}")

# --- Helper/Preparation Agent ---
# This agent's job is to find the next post, prepare content (links, image).
# Its final output should be the structured data package needed by the Posting Agent.

preparation_agent = Agent(
    name="Preparation Agent",
    instructions=f"""
    # Preparation Agent Prompt

    ## Role and Objective
    You are the Preparation Agent, responsible for selecting an approved, unpublished blog post from the `generated_posts` Google Sheet, enhancing it with links and an image, and outputting a structured string in a specific format.

    ## Instructions

    1. **Select Post**
      - Use `manage_sheet_data_tool` with:
        - `action="get_all_records"`
        - `worksheet_name="generated_posts"`
      - Filter the records to find the first row where:
        - `Approve/Disapprove` = "Approve"
        - `Published` = "No"
      - If no such row is found, return:
        ```
        STATUS: NO_POSTS_FOUND
        MESSAGE: No approved, unpublished posts found.
        ```

    2. **Extract Data**
      - Extract: `Keyword/Topic`, `Generated Content`, `FAQs`, `Approve/Disapprove`, `Published`.
      - Parse `FAQs` as JSON or Markdown (e.g., `* **Q: ...** **A:** ...`). Convert to JSON `[{{"question": "...", "answer": "..."}}]`.
      - If `FAQs` is missing or empty, use a default:
        ```json
        [
          {{
            "question": "What is the topic?",
            "answer": "About [Keyword/Topic]..."
          }}
        ]
        ```

    3. **Fetch Links**
      - Use `fetch_internal_links_tool` with `Keyword/Topic` to get related posts. Format as:
        ```markdown
        ## Related Posts
        - [Post Title](/blog/slug)
        ```
      - External links: Use any in `Generated Content` or leave `EXTERNAL_LINKS_MD` empty.

    4. **Fetch Image**
      - Use `get_stock_image_tool` with `Keyword/Topic` to get `IMAGE_URL` and `ALT_TEXT`.

    5. **Derive Fields**
      - `TITLE`: Use `Keyword/Topic` or derive a title.
      - `SUMMARY`: Take the first sentence of `Generated Content` or derive from `Keyword/Topic`.
      - `SLUG`: Create a URL-friendly slug from `Keyword/Topic` (e.g., `brand-consistency-in-social-media`).
      - `CATEGORIES`: Derive from `Keyword/Topic` (e.g., `["Social Media", "Branding"]`).
      - `CONTENT_WITH_LINKS`: Use `Generated Content`.

    6. **Output Structured String**
      Output only this multi-line string, replacing placeholders with actual values:
      ```
      === POST_DATA_START ===
      KEYWORD_TOPIC: [...]
      TITLE: [...]
      SUMMARY: [...]
      CONTENT_WITH_LINKS: [...]
      CATEGORIES: [...]
      IMAGE_URL: [...]
      ALT_TEXT: [...]
      SLUG: [...]
      INTERNAL_LINKS_MD: [...]
      EXTERNAL_LINKS_MD: [...]
      FAQS: [...]
      SOURCE_KEYWORD_TOPIC: [...]
      === POST_DATA_END ===
      ```
      Example:
      ```
      === POST_DATA_START ===
      KEYWORD_TOPIC: Brand consistency in social media
      TITLE: Brand Consistency on Social Media: Your Blueprint for Building Trust & Engagement
      SUMMARY: Ever scrolled through your feed and instantly recognized a brand...
      CONTENT_WITH_LINKS: # Brand Consistency on Social Media...
      CATEGORIES: ["Social Media", "Branding"]
      IMAGE_URL: https://images.pexels.com/photos/18610082/pexels-photo-18610082.jpeg
      ALT_TEXT: Brand consistency in social media stock image
      SLUG: brand-consistency-in-social-media
      INTERNAL_LINKS_MD: ## Related Posts
      - [Social Media Strategy](/blog/social-media-strategy)
      EXTERNAL_LINKS_MD: 
      FAQS: [{{"question": "What is brand consistency?", "answer": "It ensures a unified brand identity..."}}]
      SOURCE_KEYWORD_TOPIC: Brand consistency in social media
      === POST_DATA_END ===
      ```

    ## Tools
    - `manage_sheet_data_tool`
    - `fetch_internal_links_tool`
    - `get_stock_image_tool`
    """,
    tools=[manage_sheet_data_tool, fetch_internal_links_tool, get_stock_image_tool],
    hooks=MyAgentHooks(),
    model="gemini-2.0-flash",
    model_settings=ModelSettings(temperature=0.5),
)


# --- Posting Agent ---
# This agent receives the prepared data and handles Sanity publishing + sheet updates.

posting_agent = Agent(
    name="Posting Agent",
    instructions="""
    You are the Posting Agent. Your ONLY job is to publish blog posts to Sanity CMS and update Google Sheets.
    
    YOU MUST CALL THE `post_to_sanity_tool` TOOL. THIS IS NOT OPTIONAL.
    
    Step 1: Check input
    - If you see "STATUS: NO_POSTS_FOUND", return:
    {"status": "no_posts_found", "message": "No posts ready for publishing."}
    
    Step 2: Extract data
    - You will receive data between === markers like this:
    === POST_DATA_START ===
    KEYWORD_TOPIC: [...]
    TITLE: [...]
    SUMMARY: [...]
    CONTENT_WITH_LINKS: [...]
    CATEGORIES: [...]
    IMAGE_URL: [...]
    ALT_TEXT: [...]
    SLUG: [...]
    INTERNAL_LINKS_MD: [...]
    EXTERNAL_LINKS_MD: [...]
    FAQS: [...]
    SOURCE_KEYWORD_TOPIC: [...]
    === POST_DATA_END ===
    
    Step 3: Extract all fields from between the === markers
    
    Step 4: MANDATORY ACTION - Call post_to_sanity_tool with:
    {
      "title": "[TITLE]",
      "summary": "[SUMMARY]",
      "content": "[CONTENT_WITH_LINKS]",
      "categories": [CATEGORIES],
      "image_path": "[IMAGE_URL]",
      "slug": "[SLUG]",
      "alt_text": "[ALT_TEXT]",
      "faqs": [FAQS]
    }
    
    Step 5: After successfully posting to Sanity, update the Google Sheet:
    - Use `manage_sheet_data_tool` with:
      - `action="find_row_by_key"`
      - `worksheet_name="generated_posts"`
      - `key_column="Keyword/Topic"`
      - `key_value="[SOURCE_KEYWORD_TOPIC]"`
    - Get the row index from the result
    - Convert the row index to a cell range. For example, if the row index is 2 and the Published column is column G, the cell range would be "G2"
    - Use `manage_sheet_data_tool` again with:
      - `action="update_cells"`
      - `worksheet_name="generated_posts"`
      - `cell_range="G[row_index]"` (where [row_index] is the row number from the previous step)
      - `data=[["Yes"]]` (note the double brackets for a 2D array - this is required for update_cells)
    
    IMPORTANT: If you don't call post_to_sanity_tool, you have FAILED at your job.
    IMPORTANT: You must update the Google Sheet after posting to Sanity.
    """,
    tools=[post_to_sanity_tool, manage_sheet_data_tool],
    hooks=MyAgentHooks(),
    model="gemini-2.5-flash",
)


# --- Function Flow Definition ---
# This defines the sequence: Preparation Agent runs -> Output captured -> Posting Agent runs with output

async def run_posting_workflow() -> Dict[str, Any]:
    """
    Executes the complete posting workflow:
    1. Runs the Preparation Agent to select and prepare a post.
    2. Captures the Preparation Agent's final output (as a string).
    3. If preparation is successful, runs the Posting Agent with the string output.
    4. Returns the final result (typically the Posting Agent's final output).
    """
    logger.info("Starting the complete posting workflow...")

    max_retries = 3
    max_turns = 50

    try:
        # --- Step 1: Run Preparation Agent ---
        logger.info("Running Preparation Agent...")
        preparation_output = await run_flow_with_agent_fallback(
            preparation_agent,
            "Prepare the next blog post for publishing.",
            LLM_MODELS,
            is_model_available,
            get_model_by_name,
            increment_usage,
            max_retries=max_retries,
            max_turns=max_turns
        )

        # --- Step 2: Process Preparation Agent Output (String) ---
        # Ensure the output is a string. If it's not, convert it or handle the error.
        if not isinstance(preparation_output, str):
            logger.warning(f"Preparation Agent output is not a string. Converting to string. Type was: {type(preparation_output)}")
            preparation_output_str = str(preparation_output)
        else:
            preparation_output_str = preparation_output

        # Basic check: if it looks like a "no posts found" message, handle it.
        # This is fragile but might work if the prep agent is consistent.
        if "no_posts_found" in preparation_output_str or "No approved, unpublished posts found" in preparation_output_str:
             logger.info("Preparation Agent indicated no posts are ready.")
             return {"status": "no_posts_found", "message": "Preparation agent reported no posts available for publishing.", "details": preparation_output_str[:200]}


        # --- Step 3: Run Posting Agent with Preparation Output String ---

        # Pass the string output directly as the input to the Posting Agent
        posting_output = await run_flow_with_agent_fallback(
            posting_agent,
            preparation_output_str, # <-- Pass the string directly
            LLM_MODELS,
            is_model_available,
            get_model_by_name,
            increment_usage,
            max_retries=max_retries,
            max_turns=max_turns
        )

        logger.info("Posting workflow completed.")
        # Return the Posting Agent's output directly
        return {"status": "completed", "data": posting_output}

    except Exception as e:
        logger.error(f"Error in posting workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}