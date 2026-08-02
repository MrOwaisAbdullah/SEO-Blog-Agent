import logging
import re
from agents import Agent, ModelSettings, AgentHooks, handoff, trace
from tools.tools import post_to_sanity_tool, fetch_internal_links_tool, get_stock_image_tool # Ensure correct import paths
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
      - **CRITICAL FALLBACK**: `get_blog_image_tool`'s internal quality evaluation step is pinned to a single provider and has no fallback of its own, so it can fail outright when that provider is out of quota (you may see an error mentioning "quota exceeded" or a status like `IMAGE_GENERATION_FAILED`). If `get_blog_image_tool` errors, returns no usable `image_url`, or reports any kind of failure, do NOT give up and do NOT report the whole task as failed over this -- immediately call `get_stock_image_tool` yourself directly, with a search query derived from `TITLE` (e.g. the main topic/keyword, without brand names). Use whatever `image_url`/`alt_text` it returns instead. A generic but real stock photo is always better than aborting the publish -- getting the post published is the priority, not having a perfect image.
      - Extract the `image_url` and `alt_text` values from this JSON response for use in later steps.

    5. **Derive Fields**
      - `TITLE`: Use `Keyword/Topic` or derive a title.
    - `SUMMARY`: Ensure there is a 50–160 character SEO-friendly meta description. If the sheet includes a valid `Summary` (50–160 chars, contains primary keyword), use it. Otherwise, derive a concise meta description (50–160 chars) that includes the primary keyword, accurately summarizes the page, and is suitable for search result snippets.
      - `SLUG`: Create a URL-friendly slug from `Keyword/Topic` (e.g., `brand-consistency-in-social-media`).
      - `CATEGORIES`: Derive from `Keyword/Topic` (e.g., `["Social Media", "Branding"]`).
      - `CONTENT_WITH_LINKS`: Use `Generated Content`. TITLE is rendered as the page's own H1 above the content -- if `Generated Content` starts with a heading (`#`, `##`, or `###`) that repeats the title, remove that heading line before using it here so the title doesn't appear twice on the page. The content should start directly with the introduction, not a heading that restates the title.
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
    - `get_blog_image_tool` (try this first for an image)
    - `get_stock_image_tool` (guaranteed fallback if `get_blog_image_tool` fails for any reason -- see step 4)

    ## Critical Requirements
    - **IMPORTANT**: You MUST extract the `image_url` from the JSON response of `get_blog_image_tool` (or `get_stock_image_tool` if you had to fall back to it).
    - **IMPORTANT**: Do NOT use example URLs like `https://example.com/ai-smart-glasses.jpg`.
    - **IMPORTANT**: The `IMAGE_URL` field in your output MUST contain the actual path returned by the tool.
    """,
    tools=[manage_sheet_data_tool, fetch_internal_links_tool, get_stock_image_tool, image_selection_agent.as_tool(tool_name="get_blog_image_tool", tool_description="Selects or generates a relevant image for blog posts")],
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
       - Use `manage_sheet_data_tool` with action="find_row_by_key", worksheet_name="generated_posts", key_column="Title", key_value=SOURCE_KEYWORD_TOPIC to get the row_index.
       - Use `manage_sheet_data_tool` with action="get_range", worksheet_name="generated_posts", cell_range="1:1" to get the header row, and find the 1-based index of the "Published" column within it.
       - Use `manage_sheet_data_tool` with action="update_cell", worksheet_name="generated_posts", row_index=[row_index from find_row_by_key], col_index=[Published column index], data="Yes".
       - Do NOT use action="update_cells" or pass a sheet-qualified range like "'generated_posts'!Published" as cell_range -- that is not valid A1 notation and will fail. Use action="update_cell" with row_index/col_index as described above.
       - If this sheet update fails after retries, that is NOT a reason to report the overall task as failed -- the blog post was already published to Sanity, which is the outcome that matters. Note the sheet-update failure as a warning in your final answer, but do not use the word "error" to describe it, and clearly state that the Sanity publish itself succeeded.
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


# --- Deterministic marker parsing ---
# The Preparation -> Contextual Image Insertion -> Posting handoff chain used
# to rely on each LLM call echoing the entire === POST_DATA_START/END ===
# block (including a full multi-thousand-word blog post) back verbatim.
# Weaker fallback models (Cohere, OpenRouter free tier) routinely mangle,
# summarize, or drop the markers when asked to pass through that much text
# untouched, which surfaced in production as "Contextual agent did not
# return data with expected markers format" and a hard workflow failure.
# Parsing the block in Python -- and rebuilding it in Python before it's
# handed to the next agent -- removes the LLM as a lossy transport for data
# it already has no reason to be modifying.
_POST_DATA_FIELD_RE = re.compile(r"^([A-Z_]{2,}):\s?", re.MULTILINE)


def _parse_post_data_block(text: str) -> Optional[Dict[str, str]]:
    if not isinstance(text, str):
        return None
    start = text.find("=== POST_DATA_START ===")
    end = text.find("=== POST_DATA_END ===")
    if start == -1 or end == -1 or end <= start:
        return None
    body = text[start + len("=== POST_DATA_START ==="):end]

    matches = list(_POST_DATA_FIELD_RE.finditer(body))
    if not matches:
        return None
    fields = {}
    for i, m in enumerate(matches):
        key = m.group(1)
        value_start = m.end()
        value_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        fields[key] = body[value_start:value_end].strip()
    return fields


def _build_post_data_block(fields: Dict[str, str]) -> str:
    lines = ["=== POST_DATA_START ==="]
    for key, value in fields.items():
        lines.append(f"{key}: {value}")
    lines.append("=== POST_DATA_END ===")
    return "\n".join(lines)


_LEADING_HEADING_RE = re.compile(r"^\s*#{1,3}\s+(.+?)\s*\n", re.MULTILINE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _strip_duplicate_title_heading(content: str, title: str) -> str:
    """The page template renders TITLE as its own H1 above the content, so
    content that also opens with a heading repeating the title shows the
    title twice on the live page. content_generator_agent's instructions
    already say not to do this, but that's prompt-only guidance and this
    session has repeatedly found fallback models don't follow formatting
    instructions reliably -- confirmed live: a real published post's content
    started with "## <title text>". Strip it deterministically here instead
    of trusting the prompt alone."""
    if not content or not title:
        return content
    match = _LEADING_HEADING_RE.match(content)
    if not match:
        return content
    heading_text = _NON_ALNUM_RE.sub("", match.group(1).lower())
    title_text = _NON_ALNUM_RE.sub("", title.lower())
    if not heading_text or heading_text != title_text:
        return content
    return content[match.end():].lstrip()


def _extract_sanity_tool_output(run_result) -> Optional[dict]:
    """Finds post_to_sanity_tool's actual return value in a RunResult's tool
    call outputs, identified by its distinctive {"status", "post_id"} key
    pair (no other tool this agent uses returns that shape). Returns None if
    the tool was never called."""
    new_items = getattr(run_result, "new_items", None) or []
    for item in new_items:
        if getattr(item, "type", None) != "tool_call_output_item":
            continue
        output = getattr(item, "output", None)
        if isinstance(output, dict) and "post_id" in output and "status" in output:
            return output
    return None


def _sanity_publish_already_succeeded(run_result) -> bool:
    """Confirmed live: `if "error" not in str(posting_result).lower()` treated
    a successful Sanity publish as a failure because the agent's own summary
    text mentioned an unrelated sheet-update sub-error ("...published to
    Sanity CMS. I ... encountered an error when trying to update the
    'Published' column..."). That triggered a full-workflow retry, which
    re-ran post_to_sanity_tool and created a SECOND Sanity document for the
    same post -- create_document has no idempotency/dedup by slug, so any
    unnecessary retry after a real publish is a duplicate-publish risk, not
    just a wasted API call.

    Instead of parsing narrative text, inspect the actual tool_call_output
    items in the RunResult for post_to_sanity_tool's own return value and
    trust that instead of whatever the model said about it afterwards."""
    output = _extract_sanity_tool_output(run_result)
    return output is not None and output.get("status") == "success"


# --- Function Flow Definition ---
# This defines the sequence: Preparation Agent runs -> Output captured -> Posting Agent runs with output

async def run_posting_workflow(max_retries: int = 2) -> Dict[str, Any]:
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
            prep_fields = _parse_post_data_block(preparation_output_str)
            if not prep_fields or "CONTENT_WITH_LINKS" not in prep_fields:
                logger.error("Preparation Agent output did not contain a parseable POST_DATA block")
                return {"status": "error", "error": f"Preparation Agent output missing required POST_DATA fields: {preparation_output_str[:300]}"}

            prep_fields["CONTENT_WITH_LINKS"] = _strip_duplicate_title_heading(
                prep_fields["CONTENT_WITH_LINKS"], prep_fields.get("TITLE", "")
            )

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

            contextual_output_raw = str(getattr(contextual_result, "final_output", contextual_result))
            contextual_fields = _parse_post_data_block(contextual_output_raw)
            if contextual_fields and "CONTENT_WITH_LINKS" in contextual_fields:
                logger.info("Contextual agent returned a parseable POST_DATA block")
                post_fields = contextual_fields
            else:
                # A fallback model dropped/mangled the markers while echoing
                # back the full post. Rather than fail the whole publish over
                # a cosmetic image-insertion step, degrade gracefully: keep
                # the original prepared content (no extra contextual images)
                # and continue. The image already selected in Preparation
                # (IMAGE_URL) still gets published either way.
                logger.warning(
                    "Contextual agent did not return a parseable POST_DATA block; "
                    "continuing without contextual image insertion. Preview: %s",
                    contextual_output_raw[:200],
                )
                post_fields = prep_fields

            if "CONTENT_WITH_LINKS" in post_fields:
                post_fields["CONTENT_WITH_LINKS"] = _strip_duplicate_title_heading(
                    post_fields["CONTENT_WITH_LINKS"], post_fields.get("TITLE", "")
                )

            # Rebuild the marker block deterministically instead of trusting
            # either agent's raw text -- guarantees the Posting Agent always
            # receives a well-formed block regardless of which model ran it.
            contextual_text = _build_post_data_block(post_fields)
            has_images = bool(re.search(r"!\[.*\]\(.*\)", post_fields.get("CONTENT_WITH_LINKS", "")))
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
                    
                    # Check the actual post_to_sanity_tool output first --
                    # if Sanity already has the post, nothing here should
                    # ever trigger a retry, since a retry means calling
                    # post_to_sanity_tool (and create_document, which has no
                    # dedup) a second time for the same post.
                    if _sanity_publish_already_succeeded(posting_result):
                        logger.info("Posting Agent completed successfully (Sanity publish confirmed via tool output)")
                        break
                    if "error" not in str(posting_result).lower():
                        logger.info("Posting Agent completed successfully")
                        break
                    else:
                        logger.warning(f"Posting Agent failed on attempt {attempt + 1}: {str(posting_result)}")
                except Exception as e:
                    logger.warning(f"Posting Agent failed on attempt {attempt + 1} with exception: {str(e)}")

                if attempt < max_retries - 1:  # Don't sleep on the last attempt
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

            if posting_result is None:
                logger.error(f"Posting Agent failed after {max_retries} attempts")
                return {"status": "error", "error": f"Posting Agent failed after {max_retries} attempts: {str(posting_result)}"}
            if not _sanity_publish_already_succeeded(posting_result) and "error" in str(posting_result).lower():
                logger.error(f"Posting Agent failed after {max_retries} attempts")
                return {"status": "error", "error": f"Posting Agent failed after {max_retries} attempts: {str(posting_result)}"}

            posting_output = posting_result.final_output if hasattr(posting_result, 'final_output') else str(posting_result)

            logger.info("Posting workflow completed.")
            sanity_output = _extract_sanity_tool_output(posting_result) or {}
            return {
                "status": "completed",
                "data": posting_output,
                "title": post_fields.get("TITLE"),
                "post_url": sanity_output.get("post_url"),
                "post_id": sanity_output.get("post_id"),
            }

    except Exception as e:
        logger.error(f"Error in posting workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}