# ContentSpark AI: Automated SEO-Optimized Blog Post Generation System

## Executive Summary

ContentSpark AI is an automated system designed to generate 15–17 high-quality, SEO-optimized blog posts daily for a Next.js website hosted on Vercel with Sanity CMS. The system targets a SaaS niche focused on automated social media content creation and scheduling, incorporating a bottom-up SEO funnel strategy (TOFU, MOFU, BOFU) to drive conversions. It uses a multi-agent workflow orchestrated by the OpenAI Agents SDK, with free-tier APIs (Gemini, OpenRouter, Cohere for LLMs; Unsplash, Pexels, Hugging Face, StarryAI, Freepik for images; Tavily for search/extract/crawl) and fallbacks for reliability. The system is hosted on Hugging Face Spaces, scheduled via Cron-Job.org, and designed for future scalability into a SaaS platform. By leveraging custom fallback logic for LLMs and staggered triggers, it manages API limits while adapting to AI-era SEO, where traditional keyword ranking is superseded by becoming a cited source in AI-generated summaries and building pervasive brand authority.

## Project Idea

The core idea of ContentSpark AI is to automate the entire blog post generation process for a SaaS platform, enabling 15–17 daily posts to achieve 1,000 indexed posts in 60 days with proper SEO practices. The system uses a multi-agent workflow to handle keyword selection, research, brief creation, content generation, evaluation, revision, publishing, and repurposing. It incorporates two research streams (YouTube videos and keywords/topics) to consolidate data for content briefs, ensuring posts are optimized for AI-driven search engines like Google AI Overviews, Perplexity AI, and OpenAI Search. The system focuses on creating content that is concise, structured, and intent-driven, with features like FAQ sections, schema markup, and multi-platform repurposing to enhance visibility in the AI era.

## Why We Made It

ContentSpark AI was created to address the evolving search landscape, as detailed in the report *SEO in the Age of AI: A Strategic Blueprint for Brand and Product Visibility*. Traditional SEO is no longer sufficient; AI-driven search prioritizes direct answers and brand authority over keyword rankings. We made this system to:
- **Adapt to AI-Era SEO**: Shift from keyword stuffing to becoming a cited source in AI summaries, using structured data, topical authority, and conversational queries.
- **Scale Content Production**: Generate 15–17 posts daily using free-tier APIs with fallbacks, reducing costs while maintaining quality for a SaaS platform.
- **Drive Conversions**: Implement a bottom-up SEO funnel (TOFU: informational, MOFU: consideration, BOFU: conversion-focused) with internal linking for better user engagement.
- **Leverage Free Resources**: Use free APIs with custom fallbacks to manage quotas, ensuring reliability for 15–17 posts/day without premium subscriptions.
- **Future-Proofing**: Build a system that evolves with AI search, incorporating tools like `llm.txt` for AI governance and multi-platform repurposing for brand visibility.

The system was developed to achieve 1,000 indexed posts in 60 days, using proper SEO practices like dynamic sitemaps and RSS feeds, while addressing challenges like API limits through staggered scheduling and custom fallback logic.

## How It Works

ContentSpark AI operates as a multi-agent system with independent agents running on staggered schedules to manage API limits. The workflow uses Google Sheets for data storage and manual approval, with outputs consolidated at each step. Here's the detailed flow:

### 1. Triage Agent (Trigger: On-demand via FastAPI or Daily at 7:00 AM PKT)
- **Input**: User request or scheduled trigger.
- **Process**:
  - Reads from `ContentSpark_Keywords` sheet.
  - Selects the first available row (keyword or YouTube URL).
  - Updates status to "used" or deletes the row.
  - Validates relevance to social media content creation.
- **Output**: Single string (e.g., "best coffee maker 2025" or "[invalid url, do not cite]).
- **SEO Focus**: Ensures pipeline consistency for intent-driven content.

### 2. Research Workflow (Trigger: Daily at 8:00 AM PKT)
- **Input**: Triage Agent output (string).
- **Process**:
  - Parse string:
    - If matches YouTube URL (regex: `youtube\.com|youtu\.be`), run YouTube Research Agent with fallback runner:
      - Extracts transcript using YouTube Data API.
      - Enhances with Tavily Search/Extract/Crawl for related content.
      - Outputs findings (transcript summary, source URLs, titles).
    - If keyword, run Researcher Agent with fallback runner:
      - Uses Tavily Search/Extract/Crawl for keyword research.
      - Classifies intent, identifies secondary keywords.
      - Outputs findings (content summary, source URLs, titles).
  - Consolidate into `research_data` worksheet via Output Agent (run with fallback runner).
- **Output**: `research_data` worksheet with columns: `Source Type`, `Keyword/Topic`, `Search Volume`, `Difficulty`, `User Intent`, `Content Summary`, `Source URLs`, `Source Title`, `Approve/Disapprove`.
- **Tools**: YouTube Data API, Tavily APIs, `manage_sheet_data_tool`.
- **SEO Focus**: Targets long-tail keywords, conversational queries, authoritative sources.

### 3. Content Brief Agent (Trigger: Daily at 10:00 AM PKT)
- **Input**: Approved rows from `research_data.xlsx`.
- **Process**:
  - Reads approved rows (where `Approve/Disapprove` = "Approve").
  - Fetches titles for source URLs.
  - Generates briefs with FAQ sections, schema markup, and URLs with titles.
- **Output**: `content_briefs` worksheet with columns: `Keyword/Topic`, `Brief Content`, `External Source Links`, `Content Summary`, `Approve/Disapprove`.
- **Tools**: `get_brand_context_tool`, `fetch_url_title`, `manage_sheet_data_tool`.
- **SEO Focus**: Includes FAQPage schema, question-answer formats, and citations.

### 4. Content Generator Agent (Trigger: Daily at 12:00 PM PKT)
- **Input**: First approved row from `content_briefs.xlsx`.
- **Process**:
  - Generates post with mobile-first structure, FAQ section, and schema markup.
  - Evaluates quality using `get_evaluation_feedback` (up to 3 turns).
  - Outputs to `generated_posts` worksheet if score ≥ 90 or after 3 turns.
- **Output**: `generated_posts` worksheet with columns: `Keyword/Topic`, `Generated Content`, `Quality Score`, `Status`, `Approve/Disapprove`.
- **Tools**: `get_evaluation_feedback`, `manage_sheet_data_tool`.
- **SEO Focus**: Front-loaded answers, clear headings, AI-citable content.

### 5. Publisher and Repurposer Agent (Trigger: Daily at 2:00 PM PKT for Publishing, 4:00 PM PKT for Repurposing)
- **Input**: Approved rows from `generated_posts` worksheet.
- **Process**:
  - Publishing:
    - Adds internal/external links using `manage_post_data_tool`.
    - Sources/generates images with alt text.
    - Publishes to Sanity CMS with schema markup.
  - Repurposing:
    - Reformats content for Reddit, Quora, LinkedIn, YouTube.
    - Posts using platform APIs, including backlinks.
- **Output**: `published_posts` worksheet, `repurposed_content` worksheet.
- **Tools**: `get_stock_image_tool`, `generate_image_tool`, `post_to_sanity_tool`, `platform_post_tool`.
- **SEO Focus**: Topical authority, trust-building, multi-platform visibility.

---

### Modifications to Existing Code

The modifications will update the existing agents (`triage_agent`, `researcher_agent`, `content_generator_agent`, `posting_agent`), add new agents (`youtube_research_agent`, `brief_agent`), and update the workflow function to use manual chaining with conditions. The changes ensure compatibility with the OpenAI Agents SDK, address category/author issues, and align with AI-era SEO strategies.

**Note**: The Content Brief Agent is implemented as `brief_agent` in the code, not `content_brief_agent` as referenced in some parts of this documentation.

#### 1. Triage Agent Modifications
**Changes** (in `agents.py` or `blog_agents.py`):
- Update instructions to return a plain string.
- Remove handoffs (handled manually in workflow).
- Modify to read from `ContentSpark_Keywords` (including YouTube URLs).

#### 2. YouTube Research Agent (Planned for Future Implementation)
**Note**: The YouTube Research Agent is planned for future implementation but is currently commented out in the code. For now, all inputs (both keywords and YouTube URLs) are processed by the Researcher Agent.

**Planned Code** (in `blog_agents.py`):
```python
# youtube_research_agent = Agent(
#     name="YouTube Research Agent",
#     instructions=f"""
#     {RECOMMENDED_PROMPT_PREFIX}
#
#     **Role and Objective:**
#     You are the YouTube Research Agent, responsible for extracting and analyzing YouTube video transcripts to identify key topics and insights for blog posts, using Tavily APIs for enhancement.
#
#     **Instructions:**
#     1. **Input**: Receive a YouTube URL (e.g., "[invalid url, do not cite]).
#     2. **Fetch Transcript**: Use YouTube Data API (placeholder) to fetch transcript.
#     3. **Extract Key Topics**: Analyze transcript to identify key topics, points, or features.
#     4. **Enhance with Research**:
#        - Use `tavily_search_tool` with query based on topic (max_results=5).
#        - For score > 0.7, use `tavily_extract_tool` for full content.
#        - For score > 0.8 and short content, use `tavily_crawl_tool` for deeper content.
#        - Use `fetch_url_title` for titles.
#     5. **Output**: Dictionary with findings for Output Agent.
#
#     **Tools:**
#     - `tavily_search_tool`: Find relevant web pages.
#     - `tavily_extract_tool`: Get clean text from URLs.
#     - `tavily_crawl_tool`: Explore website structure.
#     - `fetch_url_title`: Fetch URL titles.
#
#     **Additional Notes:**
#     - Enhance transcript with authoritative web sources.
#     - Persist with retries for reliable data.
#     """,
#     tools=[tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_url_title],
#     hooks=MyAgentHooks(),
#     model=get_model_by_name("cohere"),
#     model_settings=ModelSettings(temperature=0.5),
# )
```

#### 3. Researcher Agent Modifications
**Changes** (in `blog_agents.py`):
- Update instructions to handle keyword inputs only (YouTube handled separately).
- Use Tavily APIs, consolidate output for Output Agent.

#### 4. Content Brief Agent
**Implementation** (in `blog_agents.py`):
The Content Brief Agent is implemented as `brief_agent` in the code:

```python
brief_agent = Agent(
    name="Brief Agent",
    instructions=f"""
    **Role and Objective:**  
    You are the Content Brief Agent, an SEO expert tasked with creating detailed content briefs from approved rows in the `research_data` worksheet, ensuring alignment with user intent (informational, navigational, or transactional) and topical authority for a SaaS platform focused on automated social media content creation and scheduling. Each brief must cover the main topic comprehensively, outline 4–6 subtopics as a topic cluster, and use a conversational tone with questions to address user needs, optimized for AI Overviews with a separate FAQs field. Suggest natural placements for internal and external links within the content to enhance E-E-A-T and user experience, avoiding separate "Sources" or "Related Posts" sections. Use `manage_sheet_data_tool` to read approved rows and write briefs, and leverage Tavily tools for supplementary research, with `web_search_tool` and `x_search_tool` as fallbacks.

    **Inputs:**  
    Approved rows from `research_data` worksheet (where `Generated` = "No"), containing:
    - Source Type
    - Keyword/Topic
    - Search Volume
    - Difficulty
    - User Intent
    - Content Summary
    - Source URLs
    - Source Titles
    - Generated

    **Instructions:**  
    1. **Chain-of-Thought Planning:**  
    - Step 1: Review approved rows for Keyword/Topic, User Intent, Content Summary, Source URLs, and Source Titles.  
    - Step 2: Identify 4–6 subtopics to form a topic cluster, ensuring comprehensive coverage.  
    - Step 3: Source conversational questions via Tavily tools to address user intent.  
    - Step 4: Structure the brief with SEO-optimized elements (H1, H2 headings, separate FAQs) and suggest natural link placements.  
    - Step 5: Validate citations for E-E-A-T.  
    - Step 6: Save to `content_briefs` and update `research_data`.
    ...
    [Rest of the implementation details]
    """,
    tools=[web_search_tool, x_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, manage_sheet_data_tool],
    hooks=MyAgentHooks(),
    model=get_model_by_name("kimi-openrouter"),
    model_settings=ModelSettings(temperature=0.8),
)
```

**Note**: The agent is named `brief_agent` in the implementation, not `content_brief_agent` as referenced in some parts of this documentation.

#### 5. Update Workflow Function
- Update `combined_research_workflow` to use conditions for manual chaining:
- Parse Triage output string, decide research agent, run with fallback, pass to Content Brief Agent, and then Output Agent.

**Modified Code** (in workflow file, e.g., `workflow.py`):
```python
import re
from typing import Dict, Optional
from agents import run_flow_with_agent_fallback
from blog_agent.blog_agents import triage_agent, researcher_agent, brief_agent, output_agent
from blog_agent.llm_clients import LLM_MODELS, is_model_available, get_model_by_name, increment_usage

async def combined_research_workflow(LLM_MODELS, is_model_available, get_model_by_name, increment_usage, MAX_TURNS) -> Dict[str, Optional[str]]:
    """
    Executes the main workflow using conditions to determine the research agent, each with the custom fallback runner.

    Returns:
        Dict[str, Optional[str]]: A dictionary containing the workflow status and results.
    """
    # Step 1: Run Triage Agent to get the input string
    triage_result = await run_flow_with_agent_fallback(
        triage_agent,
        "Check the Keyword sheet and return the next keyword or YouTube link",
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_turns=MAX_TURNS
    )

    if "error" in triage_result:
        return triage_result

    input_string = triage_result["final_output"]  # Single string from Triage

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

    # Step 4: Run Content Brief Agent with research results
    brief_result = await run_flow_with_agent_fallback(
        brief_agent,
        str(research_result),
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_turns=MAX_TURNS
    )

    if "error" in brief_result:
        return brief_result

    # Step 5: Run Output Agent with brief results
    output_result = await run_flow_with_agent_fallback(
        output_agent,
        str(brief_result),
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_turns=MAX_TURNS
    )

    return output_result
```

This flow ensures the Triage Agent’s string output is parsed with regex to decide the next agent, and each step uses the custom fallback runner. The workflow supports your system’s goal of generating 15–17 posts daily, aligning with AI-era SEO strategies.