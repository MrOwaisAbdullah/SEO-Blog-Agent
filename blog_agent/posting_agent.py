import logging
from agents import Agent, ModelSettings, AgentHooks, handoff, trace
from tools.tools import post_to_sanity_tool, fetch_internal_links_tool # Ensure correct import paths
from tools.sheet_tool import manage_sheet_data_tool # Ensure correct import path
from blog_agent.image_agent import image_selection_agent, contextual_image_insertion_agent  # Import the new image agent tools
from typing import Dict, Any, List, Optional
import json
import asyncio
import copy
from blog_agent.custom_runner import FallbackAgentRunner


custom_runner = FallbackAgentRunner()

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
    instructions="""
    # Preparation Agent Prompt

    ## Role and Objective
    You are the Preparation Agent, responsible for selecting an approved, unpublished blog post from the `approved_unpublished` Google Sheet, enhancing it with links and an image, and outputting a structured string in a specific format. after preparing the output you will handoff to the Contextual Image Insertion Agent to insert images into the content.

    ## Instructions

    1. **Select Post**
      - Use `manage_sheet_data_tool` with:
        - `action="get_row"`
        - `worksheet_name="approved_unpublished"`
        - `row_index=2`
      - This sheet is already filtered to only include approved, unpublished posts.
      - Fetch ONLY that single row. Do NOT call `get_all_records` or pull the entire sheet.
      - If the row is empty or missing, return:
        ```
        STATUS: NO_POSTS_FOUND
        MESSAGE: No approved, unpublished posts found.
        ```

      - **IMPORTANT**: Do not include the full list of records in your response to avoid exceeding context limits.

    2. **Extract Data**
      - Extract: `Keyword/Topic`, `Generated Content`, `FAQs`, `Approve/Disapprove`, `Published`.
      - Parse `FAQs` as JSON or Markdown (e.g., `* **Q: ...** **A:** ...`). Convert to JSON `[{"question": "...", "answer": "..."}]`.
            - Extract (include Summary/meta): `Keyword/Topic`, `Generated Content`, `FAQs`, `Summary`, `Approve/Disapprove`, `Published`. The `Summary` field must be a 50–160 character SEO-friendly meta description (if present in the sheet).
      - If `FAQs` is missing or empty, use a default:
        ```json
        [
          {
            "question": "What is the topic?",
            "answer": "About [Keyword/Topic]..."
          }
        ]
        ```

    3. **Do Not Add Any Content**
      - DO NOT add any "Related Posts" section to the content
      - DO NOT modify the original content in any way
      - Use only the links that are already in the content

    4. **Fetch Image**
    - Use `get_blog_image_tool` with `TITLE` (same as `Keyword/Topic` from index 0) and `Summary` (index 4 from sheet data). Note: `Summary` should be a 50–160 character SEO-friendly meta description; use it when available to guide image selection.
      - This tool will generate an AI image first, evaluate its quality, and use stock photos as fallback.
      - **IMPORTANT**: The tool returns a JSON response. You MUST extract the `image_url` field from this JSON response.
      - Example JSON response format:
        ```json
        {
          "image_url": "C:\\Users\\...\\temp_image.png",
          "alt_text": "Description of image",
          "source": "AI Generated",
          "evaluation_score": 8.5,
          "feedback": "Quality assessment feedback"
        }
        ```
      - Extract the `image_url` and `alt_text` values from this JSON response for use in later steps.

    5. **Derive Fields**
      - `TITLE`: Use `Keyword/Topic` or derive a title.
    - `SUMMARY`: Ensure there is a 50–160 character SEO-friendly meta description. If the sheet includes a valid `Summary` (50–160 chars, contains primary keyword), use it. Otherwise, derive a concise meta description (50–160 chars) that includes the primary keyword, accurately summarizes the page, and is suitable for search result snippets.
      - `SLUG`: Create a URL-friendly slug from `Keyword/Topic` (e.g., `brand-consistency-in-social-media`).
      - `CATEGORIES`: Derive from `Keyword/Topic` (e.g., `["Social Media", "Branding"]`).
      - `CONTENT_WITH_LINKS`: Use `Generated Content`.
      - `IMAGE_URL`: Use the `image_url` extracted from the `get_blog_image_tool` response (NOT a default/example URL).
      - `ALT_TEXT`: Use the `alt_text` extracted from the `get_blog_image_tool` response.

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
      IMAGE_URL: C:\\Users\\KTECH~1\\AppData\\Local\\Temp\\tmpnjije_gi.png
      ALT_TEXT: Brand consistency in social media illustration
      SLUG: brand-consistency-in-social-media
      FAQS: [{"question": "What is brand consistency?", "answer": "It ensures a unified brand identity..."}]
      SOURCE_KEYWORD_TOPIC: Brand consistency in social media
      === POST_DATA_END ===
      ```

    7. **Handoff**
      - After outputting the structured string, immediately handoff to the Contextual Image Insertion Agent to insert images into the content.

    ## Tools
    - `manage_sheet_data_tool`
    - `fetch_internal_links_tool`
    - `get_blog_image_tool`
    
    ## Critical Requirements
    - **IMPORTANT**: You MUST extract the `image_url` from the JSON response of `get_blog_image_tool`.
    - **IMPORTANT**: Do NOT use example URLs like `https://example.com/ai-smart-glasses.jpg`.
    - **IMPORTANT**: The `IMAGE_URL` field in your output MUST contain the actual path returned by the tool.
    """,
    tools=[manage_sheet_data_tool, fetch_internal_links_tool, image_selection_agent.as_tool(tool_name="get_blog_image_tool", tool_description="Selects or generates a relevant image for blog posts")],
    hooks=MyAgentHooks(),
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
    model_settings=ModelSettings(temperature=0.5),
)


# --- Posting Agent ---
# This agent receives the prepared data and handles Sanity publishing + sheet updates.

posting_agent = Agent(
    name="Posting Agent",
    instructions="""
    You are the Posting Agent. Your job is to publish blog posts to Sanity CMS and update Google Sheets.
    
    ## INPUT FORMAT:
    You will receive data between these markers:
    === POST_DATA_START ===
    KEYWORD_TOPIC: [value]
    TITLE: [value]
    SUMMARY: [value]
    CONTENT_WITH_LINKS: [value]
    CATEGORIES: [value]
    IMAGE_URL: [value]
    ALT_TEXT: [value]
    SLUG: [value]
    INTERNAL_LINKS_MD: [value]
    EXTERNAL_LINKS_MD: [value]
    FAQS: [value]
    SOURCE_KEYWORD_TOPIC: [value]
    === POST_DATA_END ===
    
    ## CRITICAL STEPS:
    1. FIND the data between === markers
    2. PARSE each field (TITLE, SUMMARY, CONTENT_WITH_LINKS, etc.)
     3. Validate SUMMARY and IMMEDIATELY call `post_to_sanity_tool` with these values:
         - Before posting: ensure `SUMMARY` is SEO-friendly and between 50 and 160 characters and includes the primary keyword. If the extracted `SUMMARY` does not meet these constraints, generate or trim a concise meta description that fits (50–160 chars) and accurately summarizes the page.
         - title: the extracted TITLE
         - summary: the validated or generated SUMMARY
         - content: the extracted CONTENT_WITH_LINKS (preserve all markdown formatting for proper rendering)
         - categories: the extracted CATEGORIES (as a JSON list)
         - image_path: the extracted IMAGE_URL
         - slug: the extracted SLUG
         - alt_text: the extracted ALT_TEXT
         - faqs: the extracted FAQS (as a JSON list)
    4. After successful posting, update the Google Sheet:
       - Find the row in "generated_posts" worksheet using SOURCE_KEYWORD_TOPIC to match "Title" column
       - Update the "Published" column for that row to "Yes"
    5. Record in published_posts worksheet:
       - Add a new row to "published_posts" worksheet with:
         - Keyword/Topic: SOURCE_KEYWORD_TOPIC
         - Post URL: "https://owaisabdullah.dev/blog/[SLUG]"
         - Error: empty string if successful
    
    ## MARKDOWN FORMATTING REQUIREMENTS:
    - Preserve all markdown formatting exactly as provided in CONTENT_WITH_LINKS
    - Ensure links follow format: [link text](https://example.com)
    - Ensure images follow format: ![alt text](image-url)
    - Preserve all headings, lists, bold/italic formatting
    - Do NOT modify or reformat the content - pass it exactly as received
    - All existing internal links and contextual images must be preserved
    
    ## CRITICAL:
    - Step 3 (calling post_to_sanity_tool) is REQUIRED - you MUST do this
    - After posting succeeds, do steps 4 and 5 to update sheets
    - If you skip calling post_to_sanity_tool, you have completely failed
    """,
    tools=[post_to_sanity_tool, manage_sheet_data_tool],
    hooks=MyAgentHooks(),
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
)


# --- Function Flow Definition ---
# This defines the sequence: Preparation Agent runs -> Output captured -> Posting Agent runs with output

async def run_posting_workflow(max_retries: int = 3) -> Dict[str, Any]:
    """
    Executes the complete posting workflow:
    1. Runs the Preparation Agent to select and prepare a post.
    2. The Preparation Agent automatically hands off to the Contextual Image Insertion Agent.
    3. After contextual images are inserted, the Posting Agent is called.
    4. Returns the final result.
    """
    logger.info("Starting the complete posting workflow...")

    max_turns = 50

    try:
        with trace("Posting Workflow"):
            # --- Step 1: Run Preparation Agent with retry logic ---
            # The preparation agent will handle getting the post from the sheet, 
            # preparing the data format with === markers, and automatically 
            # handing off to the contextual image agent for image insertion
            preparation_result = None
            for attempt in range(max_retries):
                try:
                    logger.info(f"Running Preparation Agent (attempt {attempt + 1}/{max_retries})...")
                    preparation_result = await custom_runner.run_with_fallback(
                        preparation_agent,
                        "Prepare the next blog post from the approved_unpublished worksheet for publishing.",
                        max_retries=max_retries,
                        max_turns=max_turns
                    )
                    
                    if "error" not in str(preparation_result).lower():
                        logger.info("Preparation Agent completed successfully")
                        break
                    else:
                        logger.warning(f"Preparation Agent failed on attempt {attempt + 1}: {str(preparation_result)}")
                except Exception as e:
                    logger.warning(f"Preparation Agent failed on attempt {attempt + 1} with exception: {str(e)}")
                
                if attempt < max_retries - 1:  # Don't sleep on the last attempt
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

            if preparation_result is None or "error" in str(preparation_result).lower():
                logger.error(f"Preparation Agent failed after {max_retries} attempts")
                return {"status": "error", "error": f"Preparation Agent failed after {max_retries} attempts: {str(preparation_result)}"}

            # Normalize the preparation result: it can be a RunResult-like object, a dict, or a string.
            def _extract_result_payload(res):
                # Try common attrs first
                payload_text = None
                payload_dict = None
                try:
                    if hasattr(res, 'final_output') and res.final_output is not None:
                        if isinstance(res.final_output, (dict, list)):
                            payload_dict = res.final_output
                        else:
                            payload_text = str(res.final_output)
                    elif hasattr(res, 'output') and res.output is not None:
                        if isinstance(res.output, (dict, list)):
                            payload_dict = res.output
                        else:
                            payload_text = str(res.output)
                    elif hasattr(res, 'input') and res.input is not None:
                        if isinstance(res.input, (dict, list)):
                            payload_dict = res.input
                        else:
                            payload_text = str(res.input)
                    elif isinstance(res, dict):
                        payload_dict = res
                    elif isinstance(res, list):
                        payload_dict = {"data": res}
                    else:
                        payload_text = str(res)
                except Exception:
                    payload_text = str(res)
                return payload_text, payload_dict

            preparation_text, preparation_dict = _extract_result_payload(preparation_result)
            logger.info(f"Preparation result (text): {preparation_text}")
            logger.info(f"Preparation result (dict): {preparation_dict}")

            # If the tool returned a structured dict with sheet data, treat that as a valid brief
            if isinstance(preparation_dict, dict):
                # Typical sheet tool shape: {"status": "success", "data": [...], "row_index": 2}
                status = preparation_dict.get('status') or preparation_dict.get('result')
                data_field = preparation_dict.get('data') if 'data' in preparation_dict else preparation_dict.get('row') if 'row' in preparation_dict else None
                if status == 'success' and data_field:
                    preparation_output = preparation_dict
                else:
                    # Fallback to text payload if dict indicates no data
                    preparation_output = preparation_text or str(preparation_result)
            else:
                preparation_output = preparation_text or str(preparation_result)

            # Basic check: if it looks like a "no posts found" message, handle it.
            preparation_output_str = str(preparation_output) if preparation_output is not None else ""
            if (isinstance(preparation_output, str) and ("no_posts_found" in preparation_output_str.lower() or "no approved, unpublished posts found" in preparation_output_str.lower() or "no approved posts found" in preparation_output_str.lower() or "status: no_posts_found" in preparation_output_str.lower())) or (
                isinstance(preparation_output, dict) and (preparation_output.get('status') == 'error' or not preparation_output.get('data'))
            ):
                logger.info("Preparation Agent indicated no posts are ready.")
                return {"status": "no_posts_found", "message": "Preparation agent reported no posts available for publishing.", "details": (str(preparation_output)[:200] if preparation_output is not None else None)}

            # At this point, preparation_output should contain the complete post data
            # in the structured === POST_DATA_START === format. Instead of relying on
            # SDK-level handoffs, explicitly run the Contextual Image Insertion Agent
            # with the preparation output, then pass that result to the Posting Agent.
            import re
            
            # Run Contextual Image Insertion Agent with retry logic
            contextual_result = None
            for attempt in range(max_retries):
                try:
                    logger.info(f"Running Contextual Image Insertion Agent (attempt {attempt + 1}/{max_retries})...")
                    contextual_result = await custom_runner.run_with_fallback(
                        contextual_image_insertion_agent,
                        preparation_output,
                        max_retries=max_retries,
                        max_turns=max_turns,
                    )
                    
                    if "error" not in str(contextual_result).lower():
                        logger.info("Contextual Image Insertion Agent completed successfully")
                        break
                    else:
                        logger.warning(f"Contextual Image Insertion Agent failed on attempt {attempt + 1}: {str(contextual_result)}")
                except Exception as e:
                    logger.warning(f"Contextual Image Insertion Agent failed on attempt {attempt + 1} with exception: {str(e)}")
                
                if attempt < max_retries - 1:  # Don't sleep on the last attempt
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

            if contextual_result is None or "error" in str(contextual_result).lower():
                logger.error(f"Contextual Image Insertion Agent failed after {max_retries} attempts")
                return {"status": "error", "error": f"Contextual Image Insertion Agent failed after {max_retries} attempts: {str(contextual_result)}"}

            # Normalize the contextual agent result
            def _extract_text(res):
                try:
                    if hasattr(res, 'final_output') and res.final_output is not None:
                        result_str = str(res.final_output)
                    elif hasattr(res, 'output') and res.output is not None:
                        result_str = str(res.output)
                    elif hasattr(res, 'input') and res.input is not None:
                        result_str = str(res.input)
                    else:
                        result_str = str(res)
                    
                    # Check if the result contains the expected markers
                    if "=== POST_DATA_START ===" in result_str and "=== POST_DATA_END ===" in result_str:
                        logger.info("Contextual agent returned data with proper markers format")
                        return result_str
                    else:
                        logger.warning("Contextual agent did not return data with expected markers format")
                        logger.info(f"Result preview: {result_str[:200]}...")
                    
                    return result_str
                except Exception as e:
                    logger.error(f"Error in _extract_text: {e}")
                    return str(res)

            contextual_text = _extract_text(contextual_result)
            has_images = bool(re.search(r"!\[.*\]\(.*\)", contextual_text))
            logger.info(f"Contextual agent produced image markdown: {has_images}")

            # Run Posting Agent with retry logic
            posting_result = None
            for attempt in range(max_retries):
                try:
                    logger.info(f"Running Posting Agent (attempt {attempt + 1}/{max_retries})...")
                    posting_result = await custom_runner.run_with_fallback(
                        posting_agent,
                        f"""Publish the following blog post:

                        {contextual_text}

                        REMEMBER: You MUST call the post_to_sanity_tool to complete the task.""",
                        max_retries=max_retries,
                        max_turns=max_turns,
                    )
                    
                    if "error" not in str(posting_result).lower():
                        logger.info("Posting Agent completed successfully")
                        break
                    else:
                        logger.warning(f"Posting Agent failed on attempt {attempt + 1}: {str(posting_result)}")
                except Exception as e:
                    logger.warning(f"Posting Agent failed on attempt {attempt + 1} with exception: {str(e)}")
                
                if attempt < max_retries - 1:  # Don't sleep on the last attempt
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

            if posting_result is None or "error" in str(posting_result).lower():
                logger.error(f"Posting Agent failed after {max_retries} attempts")
                return {"status": "error", "error": f"Posting Agent failed after {max_retries} attempts: {str(posting_result)}"}
            
            posting_output = posting_result.final_output if hasattr(posting_result, 'final_output') else str(posting_result)

            logger.info("Posting workflow completed.")
            # Return the Posting Agent's output directly
            return {"status": "completed", "data": posting_output}

    except Exception as e:
        logger.error(f"Error in posting workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}