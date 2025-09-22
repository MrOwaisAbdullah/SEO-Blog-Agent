# blog_agent/posting_agent.py

import logging
from agents import Agent, ModelSettings, AgentHooks, handoff
from tools.tools import post_to_sanity_tool, fetch_internal_links_tool, insert_contextual_images_tool # Ensure correct import paths
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
    You are the Preparation Agent, responsible for selecting an approved, unpublished blog post from the `generated_posts` Google Sheet, enhancing it with links and an image, and outputting a structured string in a specific format.

    ## Instructions

    1. **Select Post**
      - Use `manage_sheet_data_tool` with:
        - `action="get_all_records"`
        - `worksheet_name="generated_posts"`
      - **IMPORTANT**: Process the records efficiently without loading all data into your response. 
      - Filter the returned records in your mind (don't include all records in your response) to find rows where:
        - `Published` = "No" 
        - `Approve/Disapprove` is either "Approve" or "Approved" (both are acceptable)
      - Select ONLY the first matching row from this filtered list
      - If no such row is found, return:
        ```
        STATUS: NO_POSTS_FOUND
        MESSAGE: No approved, unpublished posts found.
        ```
      - **IMPORTANT**: Do not include the full list of records in your response to avoid exceeding context limits.

    2. **Extract Data**
      - Extract: `Keyword/Topic`, `Generated Content`, `FAQs`, `Approve/Disapprove`, `Published`.
      - Parse `FAQs` as JSON or Markdown (e.g., `* **Q: ...** **A:** ...`). Convert to JSON `[{"question": "...", "answer": "..."}]`.
      - If `FAQs` is missing or empty, use a default:
        ```json
        [
          {
            "question": "What is the topic?",
            "answer": "About [Keyword/Topic]..."
          }
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
      - Use `get_blog_image_tool` with `TITLE` and `Generated Content` to get a high-quality, relevant image.
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
      - `SUMMARY`: Take the first sentence of `Generated Content` or derive from `Keyword/Topic`.
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
      IMAGE_URL: C:\\Users\\KTECH~1\\AppData\\Local\\Temp\\tmpnjije_gi.png
      ALT_TEXT: Brand consistency in social media illustration
      SLUG: brand-consistency-in-social-media
      INTERNAL_LINKS_MD: ## Related Posts
      - [Social Media Strategy](/blog/social-media-strategy)
      EXTERNAL_LINKS_MD: 
      FAQS: [{"question": "What is brand consistency?", "answer": "It ensures a unified brand identity..."}]
      SOURCE_KEYWORD_TOPIC: Brand consistency in social media
      === POST_DATA_END ===
      ```

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
    model=custom_runner.get_model_by_name("gemini-2.5-flash"),
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
    
    Step 4: Separate main content from additional content
    - The CONTENT_WITH_LINKS field may contain embedded additional content (FAQs, internal links, etc.)
    - Look for common patterns that indicate embedded content:
      * FAQ sections that start with "## Frequently Asked Questions" or similar headings
      * Internal link sections that start with "## Related Posts" or similar headings
      * External link sections that start with "## External Resources" or similar headings
      * Any content that repeats what's already in the dedicated FAQ, internal links, or external links fields
    - Extract the main blog content by removing any embedded additional content
    - Ensure the main content flows naturally without embedded FAQ or link sections
    - The main content should only contain the core blog post text
    - Preserve the natural flow and structure of the content
    - Do NOT include content that duplicates the dedicated fields (FAQs, INTERNAL_LINKS_MD, EXTERNAL_LINKS_MD)
    
    Step 5: MANDATORY ACTION - Call post_to_sanity_tool with:
    {
      "title": "[TITLE]",
      "summary": "[SUMMARY]",
      "content": "[MAIN_CONTENT]",  # Only the main blog content, without embedded FAQs or links
      "categories": [CATEGORIES],
      "image_path": "[IMAGE_URL]",
      "slug": "[SLUG]",
      "alt_text": "[ALT_TEXT]",
      "faqs": [FAQS]
    }
    
    Step 6: After successfully posting to Sanity, update the Google Sheet:
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
    
    Step 7: Record published post details in the published_posts worksheet:
    - After successfully posting to Sanity, record the published post details in the `published_posts` worksheet
    - Use `manage_sheet_data_tool` with:
      - `action="append_row"`
      - `worksheet_name="published_posts"`
      - `row_values` should contain 4 columns in this order:
        1. Keyword/Topic: "[SOURCE_KEYWORD_TOPIC]"
        2. Featured Image URL: "[IMAGE_URL]" (the URL from Sanity or the local path)
        3. Post URL: "https://owaisabdullah.dev/blog/[SLUG]" (constructed by joining the base URL with the slug)
        4. Error: "" (empty string if successful, error message if failed)
    - Example tool call:
      {
        "action": "append_row",
        "worksheet_name": "published_posts",
        "row_values": ["Brand consistency in social media", "https://cdn.sanity.io/images/...", "https://owaisabdullah.dev/blog/brand-consistency-in-social-media", ""]
      }
    
    IMPORTANT: If you don't call post_to_sanity_tool, you have FAILED at your job.
    IMPORTANT: You must extract only the main blog content, not embedded FAQs or link sections.
    IMPORTANT: You must update the Google Sheet after posting to Sanity.
    IMPORTANT: You must record the published post details in the published_posts worksheet.
    IMPORTANT: Do NOT duplicate content between the main content and dedicated fields.
    """,
    tools=[post_to_sanity_tool, manage_sheet_data_tool],
    hooks=MyAgentHooks(),
    model=custom_runner.get_model_by_name("gemini-2.5-flash"),
)


# --- Function Flow Definition ---
# This defines the sequence: Preparation Agent runs -> Output captured -> Posting Agent runs with output

async def run_posting_workflow() -> Dict[str, Any]:
    """
    Executes the complete posting workflow:
    1. Runs the Preparation Agent to select and prepare a post.
    2. If preparation is successful, hands off to the Contextual Image Insertion Agent.
    3. After contextual images are inserted, hands off to the Posting Agent.
    4. Returns the final result.
    """
    logger.info("Starting the complete posting workflow...")

    max_retries = 3
    max_turns = 50

    try:
        # --- Step 1: Run Preparation Agent ---
        logger.info("Running Preparation Agent...")
        preparation_result = await custom_runner.run_with_fallback(
            preparation_agent,
            "Prepare the next blog post from the generated_posts worksheet for publishing.",
            max_retries=max_retries,
            max_turns=max_turns
        )
        
        preparation_output = preparation_result.final_output if hasattr(preparation_result, 'final_output') else str(preparation_result)

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

        # --- Step 3: Hand off to Contextual Image Insertion Agent ---
        logger.info("Handing off to Contextual Image Insertion Agent...")
        
        # Simply pass the preparation output to the contextual image insertion agent
        # The agent will extract what it needs
        contextual_result = await custom_runner.run_with_fallback(
            contextual_image_insertion_agent,
            f"Insert contextual images into the following blog post:\n\n{preparation_output_str}",
            max_retries=max_retries,
            max_turns=max_turns
        )
        
        contextual_output = contextual_result.final_output if hasattr(contextual_result, 'final_output') else str(contextual_result)

        # --- Step 4: Run Posting Agent with Contextual Images Output ---
        logger.info("Running Posting Agent with contextual images output...")
        # Pass the contextual image agent's output directly to the Posting Agent
        posting_result = await custom_runner.run_with_fallback(
            posting_agent,
            f"Publish the blog post with {contextual_output}",
            max_retries=max_retries,
            max_turns=max_turns
        )
        
        posting_output = posting_result.final_output if hasattr(posting_result, 'final_output') else str(posting_result)

        logger.info("Posting workflow completed.")
        # Return the Posting Agent's output directly
        return {"status": "completed", "data": posting_output}

    except Exception as e:
        logger.error(f"Error in posting workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}
        
        posting_output = posting_result.final_output if hasattr(posting_result, 'final_output') else str(posting_result)

        logger.info("Posting workflow completed.")
        # Return the Posting Agent's output directly
        return {"status": "completed", "data": posting_output}

    except Exception as e:
        logger.error(f"Error in posting workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}