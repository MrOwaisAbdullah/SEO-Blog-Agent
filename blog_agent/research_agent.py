from typing import Optional, Dict
import logging
import re
import asyncio
from agents import Agent, function_tool, ModelSettings
from tools.search_tools import web_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_url_title
from blog_agent.hooks import MyAgentHooks
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
from tools.sheet_tool import manage_sheet_data_tool, get_keyword_tool
from blog_agent.custom_runner import FallbackAgentRunner


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def combined_research_workflow(LLM_MODELS, is_model_available, get_model_by_name, increment_usage, MAX_TURNS, max_retries: int = 3) -> Dict[str, Optional[str]]:
    """
    Executes a combined workflow using Agents SDK, where the Triage Agent selects a keyword or link from ContentSpark_Keywords,
    the Researcher Agent conducts dual-stream research, and the Output Agent consolidates results into the research_data worksheet.

    Args:
        LLM_MODELS: List of available language models.
        is_model_available: Function to check model availability.
        get_model_by_name: Function to get a model by name.
        increment_usage: Function to increment usage metrics.
        MAX_TURNS: Maximum number of turns for the agent flow.
        max_retries: Maximum number of retries for each agent if it fails.

    Returns:
        Dict[str, Optional[str]]: A dictionary containing the workflow status and results.
    """
    # Initialize agents
    # Create a custom runner instance to get the model getter function
    custom_runner = FallbackAgentRunner()
    
    triage_agent = Agent(
        name="Triage Agent",
        instructions=f"""
        **Role and Objective:**
    You are the Triage Agent for SEO Blog Generation, responsible for selecting the next keyword or YouTube link for research from a Google Sheets list, ensuring a consistent content pipeline.

    **Instructions:**
    When user says 'Generate a post' or on schedule:
    1. **Access Keywords Sheet**: Use the Google Sheets tool to connect to the "ContentSpark_Keywords" sheet.
    2. **Select Input**:
       - Identify the first row where the "Status" column is "available."
       - If no "Status" column exists, select the first row.
       - Extract the value from the "Keyword" column (e.g., "AI social media tools for agencies" or a YouTube URL like "[invalid url, do not cite]).
    3. **Update Sheet**:
       - Set the "Status" column to "used."
       - If no "Status" column exists, delete the row to prevent reuse.
    4. **Validation**:
       - Ensure the input is a non-empty string relevant to social media content creation or scheduling.
       - Do not assume or generate inputs; use only sheet data.
       - The keyword may contain names of tools or services (e.g., "Tavily - The Web Access Layer for AI Agents") which should be treated as the research subject, not as a reference to the tools you are using.
    5. **Output**:
       - Return the input as a plain string, either the keyword (e.g., "best coffee maker 2025") or the YouTube URL (e.g., "[invalid url, do not cite).
       - Make sure to return the EXACT keyword as it appears in the sheet, without modification.

    **Tools:**
    - Google Sheets tool `get_keyword_tool`: Read/write access to "ContentSpark_Keywords".


    **Additional Notes:**
    - Avoid hallucination by using only the sheet's data.
    - Log retries internally for debugging.
    - Confirm sheet update before outputting.
    - Return the exact keyword from the sheet, even if it contains the names of tools you will be using in research.

    **Output (String):**
    "AI social media tools for agencies"
    """,
        tools=[get_keyword_tool],
        hooks=MyAgentHooks(),
        model=custom_runner.get_model_by_name("gemini-2.5-flash"),
        model_settings=ModelSettings(temperature=0.5),
    )

    # youtube_research_agent = Agent(
    #     name="YouTube Research Agent",
    #     instructions="""
    #     **Role and Objective:**
    #     You are the YouTube Research Agent, responsible for extracting and analyzing YouTube video transcripts to identify key topics and insights for blog posts.

    #     **Instructions:**
    #     1. **Input**: Receive a YouTube URL or video ID.
    #     2. **Fetch Transcript**: Use the get_youtube_transcript tool to fetch the transcript of the video.
    #     3. **Extract Key Topics**: Analyze the transcript to identify key topics, points, or features discussed in the video.
    #     4. **Output**: Return a dictionary with:
    #         - "main_topic": The main topic or keyword from the transcript.
    #         - "summary": A brief summary of the content (100-150 words).
    #         - "source_urls": List of URLs where the transcript was sourced.
    #         - "source_titles": List of titles corresponding to those URLs.
    #         - "user_intent": User intent classification (e.g., informational, navigational, transactional).
    #         - "search_volume": Search volume for the main topic (if available).
    #         - "difficulty": Difficulty score for ranking (if available).

    #     **Tools**:
    #     - `get_youtube_transcript`: Fetch the transcript of a YouTube video.
    #     - `fetch_url_title`: Fetch titles for URLs.
    #     - `tavily_search_tool`: Find relevant web pages (1 credit/query).  
    #     - `tavily_extract_tool`: Get clean text from URLs (1 credit/5 URLs).  
    #     - `tavily_crawl_tool`: Explore website structure (1 credit/5 URLs).  
    #     - `fetch_url_title`: Fetch URL titles and snippets.  


    #     """,
    #     tools=[get_youtube_transcript, fetch_url_title, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool],
    #     hooks=MyAgentHooks(),
    #     model=get_model_by_name("cohere"),
    #     model_settings=ModelSettings(temperature=0.5),
    # )



    researcher_agent = Agent(
        name="Researcher Agent",
        instructions=f"""
        {RECOMMENDED_PROMPT_PREFIX}

        **Role and Objective:**  
        You are the Researcher Agent, an SEO expert tasked with conducting dual-stream research based on the input provided (YouTube transcript text, or keyword/topics) to identify high-value, user-intent-driven content opportunities for blog posts, ensuring topical authority and AI citation potential.

        **Important Clarification for Keyword Research:**
        When conducting keyword research, you will receive a specific keyword or topic to research. The input will begin with "This is the keyword or URL to research:" followed by the actual keyword or URL. Even if the keyword contains the name of a tool or service you are also using (such as "Tavily"), you should treat the entire input as the research subject. For example, if you receive "This is the keyword or URL to research: Tavily - The Web Access Layer for AI Agents (Service Tool)", you should research this specific topic/service, not treat "Tavily" as a reference to the tool you are using.

        **Example:**
        If the input keyword is "This is the keyword or URL to research: Tavily - The Web Access Layer for AI Agents (Service Tool)", your task is to research this specific service/tool, not to use the name "Tavily" as a reference to the search tool you are using. You should use your research tools to find information about the Tavily service itself.

        **Inputs:**  
        - **Research Request**: A string that begins with "This is the keyword or URL to research:" followed by the actual keyword or URL to research. For example: "This is the keyword or URL to research: Tavily - The Web Access Layer for AI Agents (Service Tool)"

        **Instructions:**  
        1. **Chain-of-Thought Planning:**  
        - Step 1: Extract the actual keyword or points from the input (everything after "This is the keyword or URL to research:")
        - Step 2: Identify the research goal (enhance content brief with YouTube insights if URL provided, or find high-value keywords).  
        - Step 3: Select tools (Tavily primary, SerpApi/X API fallbacks).  
        - Step 4: Analyze user intent for keywords (informational, navigational, transactional).  
        - Step 5: Consolidate findings for topical authority and AI citation.  
        2. **Extract key topics/points (e.g., "Nespresso features").**
        - Enhance with Tavily tools:  
            - Call `tavily_search_tool` with query `[topic]` (max_results=5, topic="general").  
            - For YouTube URLs in results, use `tavily_extract` for additional context (exclude images).  
            - For non-YouTube results with score > 0.7, use `tavily_extract`; if score > 0.8 and content < 50 words, use `tavily_crawl` (max_depth=2, limit=10).  
            - Use `fetch_url_title` to fetch titles for all source URLs.  
        - Fallback: If Tavily fails, use X API for trending discussions (past 7 days) or SerpApi for web content.  
        - Summarize findings (100–150 words), retaining source URLs and titles for fact-checking.  
        3. **Keyword Research Process (if keyword provided):**  
        - Call `tavily_search_tool` with query `[keyword]` (max_results=5).  
        - Analyze results:  
            - For score > 0.7, use `tavily_extract` for full content.  
            - For score > 0.8 and content < 50 words, use `tavily_crawl` (max_depth=2, limit=10).  
            - Use `fetch_url_title` to fetch titles.  
        - Fallback: If Tavily fails, use SerpApi to fetch search volume, difficulty, People Also Ask, and related searches, and X API for trending topics (past 7 days).  
        - Classify user intent using OpenAI Agents SDK (e.g., "commercial: buy coffee maker").  
        4. **Output:**  
        - Return a dictionary with a "data" key containing a single comprehensive finding that consolidates all research about the keyword/topic, including fields like "main_topic/keyword", "researched content", "source_urls", "source_titles", "user_intent", "search_volume", and "difficulty" as applicable. 
        - CRITICAL: Research only the specific keyword or topic provided and return exactly ONE comprehensive finding, NOT multiple findings.

        **Efficient Data Handling:**
        When conducting research, be mindful of context window limitations. Focus on the specific keyword or URL provided and avoid loading unnecessary data. Use targeted search queries to get relevant information without overwhelming the context.

        **Tools:**  
        - `tavily_search_tool`: Find relevant web pages (1 credit/query).  
        - `tavily_extract_tool`: Get clean text from URLs (1 credit/5 URLs).  
        - `tavily_crawl_tool`: Explore website structure (1 credit/1 URLs).  
        - `fetch_url_title`: Fetch URL titles and snippets.  
        - SerpApi `web_search_tool`(fallback): Keyword data.  
        """,
        tools=[web_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_url_title],
        hooks=MyAgentHooks(),
        model=custom_runner.get_model_by_name("gemini-2.5-flash"),
        model_settings=ModelSettings(temperature=0.5),
    )

    output_agent = Agent(
        name="Output Agent",
        instructions="""
        **Role and Objective:**
        You are the Output Agent for ContentSpark AI, responsible for consolidating research findings into the `research_data` worksheet.

        **Important Note:**
        You may receive research findings about services or tools that share names with the tools you are using. For example, you might receive research about "Tavily" as a service, while also using a `tavily_search_tool`. Treat all research findings as content about the subject being researched, not as references to your tools.

        **Efficient Data Handling Instructions:**
        To avoid loading unnecessary data and preserve context window space:
        1. **Before Adding Data**: Do not load existing records from the worksheet. Simply append new findings.
        2. **When Adding New Findings**: Use `manage_sheet_data_tool` with action="append_row" to add each finding directly without loading existing data.
        3. **Always**: Add data in the correct column order and format as specified in the example.

        **Instructions:**
        1. **Process Input**: Receive a dictionary with a "data" key containing a list of research findings from the Researcher Agent.
        2. **Consolidate Output**:
        - CRITICAL: Only create ONE row per keyword/topic. If the "data" list contains multiple findings for the same keyword/topic (which should not happen with the updated researcher), only process the FIRST finding in the list.
        - Use `manage_sheet_data_tool` with action="append_row" to write the single finding to the `research_data` worksheet.
        - Use the following columns:
          - Keyword/Topic (main topic or keyword from finding)
          - Search Volume (from finding, or "N/A" for YouTube)
          - Difficulty (from finding, or "N/A" for YouTube)
          - User Intent (from finding, or "N/A" for YouTube)
          - Content Summary (from finding)
          - Source URLs (from finding) - IMPORTANT: Convert list of URLs to a single comma-separated string
          - Source Titles (from finding) - IMPORTANT: Convert list of titles to a single comma-separated string
          - Generated (set to "No" for manual review)
        3. **Validation:**
        - Ensure all required fields are present or default to "N/A" where applicable.
        - Do not fabricate data; rely on the input findings.
        - If the Keyword/Topic contains tool names (e.g., "Tavily"), store it exactly as provided.
        - IMPORTANT: When passing lists (like Source URLs or Source Titles), convert them to comma-separated strings before passing to the tool.

        **Tools:**
        - `manage_sheet_data_tool`: Worksheet operations (e.g., action="append_row").

        Example tool call arguments:
        ```
        {
          "worksheet_name": "research_data",
          "action": "append_row",
          "row_values": [
            "YouTube",
            "Coffee Maker",
            "1000",
            "0.5",
            "commercial",
            "A brief detailed summary of the content",
            "https://example.com, https://example2.com",  // Convert list to comma-separated string
            "Example Title, Another Title",  // Convert list to comma-separated string
            "No"
          ]
        }
        ```

        """,
        tools=[manage_sheet_data_tool],
        hooks=MyAgentHooks(),
        model=custom_runner.get_model_by_name("cohere"),
        model_settings=ModelSettings(temperature=0.5),
    )

    # Step 1: Run Triage Agent to get the input with retry logic
    triage_result = None
    for attempt in range(max_retries):
        try:
            logger.info(f"Running Triage Agent (attempt {attempt + 1}/{max_retries})...")
            triage_result = await custom_runner.run_with_fallback(
                triage_agent,
                "Check the Keyword sheet and return the next keyword or topic",
                max_turns=MAX_TURNS
            )
            
            if "error" not in str(triage_result):
                logger.info("Triage Agent completed successfully")
                break
            else:
                logger.warning(f"Triage Agent failed on attempt {attempt + 1}: {str(triage_result)}")
        except Exception as e:
            logger.warning(f"Triage Agent failed on attempt {attempt + 1} with exception: {str(e)}")
        
        if attempt < max_retries - 1:  # Don't sleep on the last attempt
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    if triage_result is None or "error" in str(triage_result):
        return {"error": f"Triage Agent failed after {max_retries} attempts: {str(triage_result)}"}

    input_string = triage_result.final_output if hasattr(triage_result, 'final_output') else str(triage_result)  # The output is a single string

    # Step 2: For now, always use the Researcher Agent regardless of input type
    # (youtube_research_agent is commented out)
    research_agent = researcher_agent
    # Add context to make it clear this is the research subject
    research_input = f"This is the keyword or URL to research: {input_string}\n\nPlease conduct thorough research on this topic and provide detailed findings."

    # Step 3: Run the appropriate Research Agent with fallback logic and retry
    research_result = None
    for attempt in range(max_retries):
        try:
            logger.info(f"Running Research Agent (attempt {attempt + 1}/{max_retries})...")
            research_result = await custom_runner.run_with_fallback(
                research_agent,
                research_input,
                max_turns=MAX_TURNS
            )
            
            if "error" not in str(research_result):
                logger.info("Research Agent completed successfully")
                break
            else:
                logger.warning(f"Research Agent failed on attempt {attempt + 1}: {str(research_result)}")
        except Exception as e:
            logger.warning(f"Research Agent failed on attempt {attempt + 1} with exception: {str(e)}")
        
        if attempt < max_retries - 1:  # Don't sleep on the last attempt
            await asyncio.sleep(2 ** attempt)  # Exponential backoff

    if research_result is None or "error" in str(research_result):
        return {"error": f"Research Agent failed after {max_retries} attempts: {str(research_result)}"}

    # Step 4: Run Output Agent with research results with retry logic
    output_input = f"Here are the research findings that need to be consolidated into the research_data worksheet:\n\n{str(research_result)}\n\nPlease process these findings and add them to the worksheet using efficient data handling - append rows directly without loading all existing data."
    
    output_result = None
    for attempt in range(max_retries):
        try:
            logger.info(f"Running Output Agent (attempt {attempt + 1}/{max_retries})...")
            output_result = await custom_runner.run_with_fallback(
                output_agent,
                output_input,  # Pass the research results as a string
                max_turns=MAX_TURNS
            )
            
            if "error" not in str(output_result):
                logger.info("Output Agent completed successfully")
                break
            else:
                logger.warning(f"Output Agent failed on attempt {attempt + 1}: {str(output_result)}")
        except Exception as e:
            logger.warning(f"Output Agent failed on attempt {attempt + 1} with exception: {str(e)}")
        
        if attempt < max_retries - 1:  # Don't sleep on the last attempt
            await asyncio.sleep(2 ** attempt)  # Exponential backoff

    if output_result is None or "error" in str(output_result):
        return {"error": f"Output Agent failed after {max_retries} attempts: {str(output_result)}"}

    return output_result