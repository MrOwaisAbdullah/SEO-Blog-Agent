from typing import Optional, Dict
import logging
import re
from agents import Agent, function_tool, ModelSettings
from tools.search_tools import web_search_tool, x_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_url_title
from blog_agent.blog_agents import MyAgentHooks
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
from blog_agent.llm_clients import run_flow_with_agent_fallback
from tools.sheet_tool import manage_sheet_data_tool, get_keyword_tool


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def combined_research_workflow(LLM_MODELS, is_model_available, get_model_by_name, increment_usage, MAX_TURNS) -> Dict[str, Optional[str]]:
    """
    Executes a combined workflow using Agents SDK, where the Triage Agent selects a keyword or link from ContentSpark_Keywords,
    the Researcher Agent conducts dual-stream research, and the Output Agent consolidates results into the research_data worksheet.

    Args:
        LLM_MODELS: List of available language models.
        is_model_available: Function to check model availability.
        get_model_by_name: Function to get a model by name.
        increment_usage: Function to increment usage metrics.
        MAX_TURNS: Maximum number of turns for the agent flow.

    Returns:
        Dict[str, Optional[str]]: A dictionary containing the workflow status and results.
    """
    # Initialize agents
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
    5. **Output**:
       - Return the input as a plain string, either the keyword (e.g., "best coffee maker 2025") or the YouTube URL (e.g., "[invalid url, do not cite]).

    **Tools:**
    - Google Sheets tool `get_keyword_tool`: Read/write access to "ContentSpark_Keywords".


    **Additional Notes:**
    - Avoid hallucination by using only the sheet's data.
    - Log retries internally for debugging.
    - Confirm sheet update before outputting.

    **Output (String):**
    "AI social media tools for agencies"
    """,
        tools=[get_keyword_tool],
        hooks=MyAgentHooks(),
        model=get_model_by_name("gemini-2.5-flash-lite"),
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
        You are the Researcher Agent, an SEO expert tasked with conducting dual-stream research based on the input provided (YouTube transcript if a URL is given, and keyword/topics analysis) to identify high-value, user-intent-driven content opportunities for blog posts, ensuring topical authority and AI citation potential.

        **Inputs:**  
        - **Keyword or Link**: A string that can be a keyword (e.g., "best coffee maker 2025") or a YouTube URL (e.g., "https://www.youtube.com/watch?v=example") provided by the Triage Agent.

        **Instructions:**  
        1. **Chain-of-Thought Planning:**  
        - Step 1: Identify the research goal (enhance content brief with YouTube insights if URL provided, or find high-value keywords).  
        - Step 2: Select tools (Tavily primary, SerpApi/X API fallbacks).  
        - Step 3: Analyze user intent for keywords (informational, navigational, transactional).  
        - Step 4: Consolidate findings for topical authority and AI citation.  
        2. **YouTube Research Process (if URL provided):**  
        - Fetch transcript via YouTube Data API v3 from the provided URL.  
        - Extract key topics/points (e.g., "Nespresso features").  
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
        - Return a dictionary with a "data" key containing a list of findings, where each finding includes fields like "main_topic/keyword", "summary", "source_urls", "source_titles", "user_intent", "search_volume", and "difficulty" as applicable.

        **Tools:**  
        - `tavily_search_tool`: Find relevant web pages (1 credit/query).  
        - `tavily_extract_tool`: Get clean text from URLs (1 credit/5 URLs).  
        - `tavily_crawl_tool`: Explore website structure (1 credit/5 URLs).  
        - `fetch_url_title`: Fetch URL titles and snippets.  
        - SerpApi `web_search_tool`(fallback): Keyword data.  
        - X API `x_search_tool`(fallback): Trending discussions.
        """,
        tools=[web_search_tool, x_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_url_title],
        hooks=MyAgentHooks(),
        model=get_model_by_name("cohere"),
        model_settings=ModelSettings(temperature=0.5),
    )

    output_agent = Agent(
        name="Output Agent",
        instructions="""
        **Role and Objective:**\
        You are the Output Agent for ContentSpark AI, responsible for consolidating research findings into the `research_data` worksheet.

        **Instructions:**
        1. **Process Input**: Receive a dictionary with a "data" key containing a list of research findings from the Researcher Agent.
        2. **Consolidate Output**:
        - For each finding in the "data" list, use `manage_sheet_data_tool` with action="append_row" to write to the `research_data` worksheet.
        - Use the following columns:
          - Keyword/Topic (main topic or keyword from finding)
          - Search Volume (from finding, or "N/A" for YouTube)
          - Difficulty (from finding, or "N/A" for YouTube)
          - User Intent (from finding, or "N/A" for YouTube)
          - Content Summary (from finding)
          - Source URLs (from finding)
          - Source Titles (from finding)
          - Approve/Disapprove (empty for manual review)
          - Generated (set to "no" for manual review)
        3. **Validation**:
        - Ensure all required fields are present or default to "N/A" where applicable.
        - Do not fabricate data; rely on the input findings.

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
            "A brief summary of the content",
            ["https://example.com"],
            ["Example Title"],
            "",
            "no"
          ]
        }
        ```

        """,
        tools=[manage_sheet_data_tool],
        hooks=MyAgentHooks(),
        model=get_model_by_name("cohere"),
        model_settings=ModelSettings(temperature=0.5),
    )

# Step 1: Run Triage Agent to get the input
    triage_result = await run_flow_with_agent_fallback(
        triage_agent,
        "Check the Keyword sheet and return the next keyword or topic",
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_turns=MAX_TURNS
    )

    if "error" in triage_result:
        return triage_result

    input_string = triage_result["final_output"]  # The output is a single string

    # Step 2: For now, always use the Researcher Agent regardless of input type
    # (youtube_research_agent is commented out)
    research_agent = researcher_agent
    research_input = input_string  # Pass the input directly (keyword or URL)

    # Step 3: Run the appropriate Research Agent with fallback logic
    research_result = await run_flow_with_agent_fallback(
        research_agent,
        research_input,
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_turns=MAX_TURNS
    )

    if "error" in research_result:
        return research_result

    # Step 4: Run Output Agent with research results
    output_result = await run_flow_with_agent_fallback(
        output_agent,
        str(research_result),  # Pass the research results as a string
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_turns=MAX_TURNS
    )

    return output_result

# Example usage (if run directly with an async event loop)
if __name__ == "__main__":
    import asyncio
    result = asyncio.run(combined_research_workflow(LLM_MODELS=[], is_model_available=lambda x: True, get_model_by_name=lambda x: None, increment_usage=lambda: None, MAX_TURNS=10))
    print(result)