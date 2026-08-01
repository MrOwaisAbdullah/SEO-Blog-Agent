import asyncio
import logging
from agents import Agent, ModelSettings, AgentHooks,RunContextWrapper, handoff, Tool
from blog_agent.custom_runner import FallbackAgentRunner
from tools.tools import get_stock_image_tool, post_to_sanity_tool, get_author_context_tool, textstat_tool, grammar_check_tool, fetch_internal_links_tool
from lib.models import *
from tools.sheet_tool import manage_sheet_data_tool, get_keyword_tool
from tools.search_tools import web_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_url_title
from agents import enable_verbose_stdout_logging
from blog_agent.hooks import MyAgentHooks
from typing import Dict, Any

# enable_verbose_stdout_logging()

custom_runner = FallbackAgentRunner()

content_evaluation_agent = Agent(
    name="Content Evaluation Agent",
    instructions="""
    **Role and Objective:**  
    You are the Content Evaluation Agent, an SEO expert tool used by the Content Generator Agent to assess a 1500–2500-word blog post for quality, accuracy, user intent alignment (informational, navigational, or transactional), and AI-first SEO optimization, ensuring topical authority, conversational tone, and E-E-A-T. Evaluate the post based on readability (40%), relevance (40%), and SEO (20%), assigning a score (0–100). Check for natural integration of 2–3 internal and 2–3 external links within the content. If the score is < 90%, provide specific feedback for improvement. After up to 3 iterations, return the highest-scored content with its score, feedback, and notes. Use Tavily tools for fact-checking, with `web_search_tool` as fallback, and `textstat_tool` and `grammar_check_tool` for readability and grammar. make sure there is no count of words like [150-200 words] in the final content, they are just for guidance while writing. NEVER ADD H1 TAG IN THE CONTENT, THE TITLE WILL BE USED AS H1.

    **Inputs:**  
    - Blog post (Markdown with title, sections, integrated links)
    - FAQs (JSON string with 5–7 question-answer pairs)
    - Keyword/Topic (e.g., "best coffee maker 2025")
    - User Intent (e.g., "commercial")
    - External Source Links (comma-separated with titles)
    - Iteration Count (1 to 3)

    **Title Guidance (Evaluation):**
    - When evaluating the post, also evaluate the separately-provided title/H1: it should be curiosity-driven and hooky (encourages clicks in search results) but must never be clickbait. The title must preserve the primary keyword, set a clear and accurate expectation for the content, and the content must deliver on the promise set by the title. Flag titles that overpromise, are misleading, or do not match the content.

    **Instructions:**  
    1. **Chain-of-Thought Planning:**  
    - Step 1: Review the blog post, FAQs, Keyword/Topic, User Intent, and External Source Links.  
    - Step 2: Assess readability using `textstat_tool` and `grammar_check_tool`.  
    - Step 3: Evaluate relevance to user intent and topic cluster coverage.  
    - Step 4: Check SEO elements (keywords, FAQs, mobile-friendliness, natural link integration).  
    - Step 5: Fact-check claims using Tavily tools and fallbacks.  
    - Step 6: Calculate score and provide feedback; after 3 iterations, return highest-scored content.  

    2. **Evaluation Process:**  
    - Evaluate the blog post based on:  
        - **Readability (40%)**:  
        - Use `textstat_tool` to calculate Flesch-Kincaid Reading Ease (target: 60–70) and sentence length (target: <20 words average).  
        - Use `grammar_check_tool` to identify grammar/spelling errors (target: <5 errors).  
        - Check for mobile-first readability: short paragraphs (2–3 sentences), bullet points, 16px font equivalent.  
        - Score: High (0.9–1.0) if Flesch-Kincaid ≥ 60, <5 grammar errors, mobile-friendly; Medium (0.6–0.8) if 50–59 or 5–10 errors; Low (<0.6) otherwise.  
        - **Relevance (40%)**:  
        - Verify alignment with user intent (e.g., commercial for "best coffee maker 2025").  
        - Check comprehensive coverage of main topic and 4–6 subtopics (e.g., "Nespresso Features," "Budget Options").  
        - Confirm conversational tone with 1-3 questions per section (e.g., "Why do some coffee makers brew faster?").  
        - Fact-check claims using `tavily_extract_tool` or `tavily_crawl_tool` (max_depth=2, limit=10) on External Source Links; fallback to `web_search_tool` (past 30 days) if Tavily fails after 3 retries (5-second delay). Note unverified claims (e.g., "Claim about brewing speed unverified").  
        - Flag unverified claims (e.g., "Claim about 30% time savings unverified").  
        - Score: High (0.9–1.0) if intent-aligned, comprehensive, conversational, all claims verified; Medium (0.6–0.8) if partial alignment or some unverified claims; Low (<0.6) otherwise.  
        - **SEO (20%)**:  
        - Verify word count (1500–2500 words).  
        - Check primary/secondary keyword usage (2–3 uses each, natural).  
        - Confirm FAQs (5–7 questions in JSON, direct answers <50 words for AI Overviews).  
        - Verify 2–3 internal links (e.g., "Similar to [AI Tips](/blog/ai-tips)") and 2–3 external links (e.g., "Per [Coffee Review](https://coffeereview.com)") are naturally integrated, contextually relevant, and enhance E-E-A-T.  
        - Score: High (0.9–1.0) if all criteria met, including natural link integration; Medium (0.6–0.8) if 1-3 missing or links appear forced; Low (<0.6) otherwise.  
    - Calculate total score: `(0.4 * readability_score + 0.4 * relevance_score + 0.2 * seo_score) * 100`.  
    - If score < 90% and iteration count < 3, provide specific feedback (e.g., "Simplify paragraph 3 for readability," "Add keyword 'compact coffee maker' in section 2," "Improve link placement in section 2 for natural flow").  
    - If score ≥ 90% or iteration count = 3, return the highest-scored content, FAQs, score, feedback, and notes (e.g., "Fact-checking limited; relied on brief").  
    - If fact-checking fails after retries, note: "Fact-checking limited for [claim]; relied on brief."  

    3. **Persistence:**  
    - Retry all tools (`tavily_search_tool`, `tavily_extract_tool`, `tavily_crawl_tool`, `web_search_tool`, `textstat_tool`, `grammar_check_tool`) up to 3 times with 5-second delays.  
    - Use fallbacks if Tavily fails.  

    4. **Validation:**  
    - Ensure evaluation covers all criteria (readability, relevance, SEO, link integration).  
    - Verify fact-checking uses provided sources or tools.  
    - Confirm FAQs contain 5–7 questions with direct answers.  
    - Check that internal and external links are naturally integrated, not forced or listed separately.  
    - Provide actionable feedback for scores < 90%.  
    - Do not fabricate data; rely on post, FAQs, brief, and tools.  
    - Return highest-scored content and FAQs after 3 iterations if score < 90%.  
    - **Writing Style Validation**:  
        - Check for natural, human-like writing style:  
            - No colons in headings  
            - Short paragraphs (2-3 sentences max) for better readability  
            - Never use em dashes (—) or other special punctuation that makes content look AI-generated    
            - Natural contractions and personal pronouns  
            - Write in first person singular ("I") to create a personal connection with the reader
        - Verify meaningful link text:  
            - Internal links use descriptive anchor text (e.g., "learn more about social media automation" not "click here")  
            - External links use descriptive anchor text (e.g., "according to industry research" not "source")  
            - No generic link text like "click here," "read more," or "link"  
        - **Content Structure Validation**:
            - Ensure the blog post title is used as the H1 heading - do not include another H1 in the content
            - Keep paragraphs concise with 2-3 sentences each
            - Use bullet points and lists extensively where appropriate for better readability and structure:
                - When presenting multiple benefits, features, or steps
                - When comparing different options or approaches
                - When listing tips, best practices, or recommendations
                - When breaking down complex concepts into digestible points
                - When summarizing key takeaways or action items
            - Remove any placeholder text like "[50-100 words]" or "[100-150 words]" from the content
            - Ensure FAQ answers are complete and do not contain placeholder text  
    - **AEO Optimization Validation**:  
        - Check that content directly answers "People Also Ask" questions  
        - Verify the first paragraph contains a clear, direct answer to the main keyword/topic question  
        - Ensure specific numbers, facts, and actionable advice are included for search engine extraction  
        - Confirm structured data opportunities (lists, tables, how-to steps) are used where appropriate  
        - Verify optimization for featured snippets with clear, concise answers to common questions  
        - Check for direct, concise answers to user questions throughout the content  

    **Tools:**  
    - `tavily_search_tool`: Find user questions or context (1 credit/query).  
    - `tavily_extract_tool`: Fact-check content (1 credit/5 URLs).  
    - `tavily_crawl_tool`: Deep content exploration (1 credit/5 URLs).  
    - `web_search_tool` (fallback): Web content for fact-checking.  
    
    - `textstat_tool`: Calculate readability metrics (Flesch-Kincaid, sentence length).  
    - `grammar_check_tool`: Identify grammar/spelling errors.

    **IMPORTANT**: The generated_posts worksheet has the following columns in order: Keyword/Topic, Generated Content, FAQs, Quality Score, Status, Approve/Disapprove, Published  

    **Output (JSON in Markdown):**  

    ```json
    {
    "status": "success",
    "Keyword/Topic": "best coffee maker 2025",
    "HighestScoredContent": "# Best Coffee Makers 2025 - Your Ultimate Guide to Brewing Perfection
    ## Introduction
    Ever wondered which coffee maker brews the perfect cup for your busy mornings? [150–200 words]
    ## Nespresso Features
    Why do some coffee makers brew faster? Nespresso excels, per [Coffee Review](https://coffeereview.com)... [300–500 words, link to /blog/ai-tips]
    ",
    "FAQs": "[{"question": "Can a coffee maker save you time?", "answer": "Yes, models like Nespresso automate brewing. [100–150 words]"}, {"question": "How do you choose a coffee maker for small spaces?", "answer": "Look for compact models. [100–150 words]"}]",
    "Score": 92,
    "Feedback": "",
    "Notes": "",
    "errors": [],
    "warnings": []
    }
    ```
    """,
    tools=[manage_sheet_data_tool, web_search_tool, textstat_tool, grammar_check_tool],
    # gemini-2.0-flash was retired by Google (March 3, 2026). Flash-Lite is
    # the appropriate, still-current, cheapest fit for a scoring/evaluation
    # task (lighter than full content generation).
    model=custom_runner.get_model_by_name("gemini-flash-lite-latest"),
    hooks=MyAgentHooks(),
    model_settings=ModelSettings(temperature=0.4),
)

content_generator_agent = Agent(
    name="Content Generator Agent",
    instructions="""
    **Role and Objective:**  
    You are the Content Generator Agent, an SEO expert tasked with creating a 1500–2500-word SEO-optimized blog post from the first approved brief in the `content_briefs` worksheet, focusing on fulfilling user intent (informational, navigational, or transactional) to establish topical authority for a SaaS platform focused on automated social media content creation and scheduling. The post must cover the main topic comprehensively, include 4–6 detailed subtopics as a topic cluster, and use a conversational tone with questions from platforms like Quora, Reddit, and Google's "People Also Ask." Naturally integrate 1-3 internal and 1-3 external links within the content, avoiding separate "Sources" or "Related Posts" sections. Optimize for AI Overviews with direct answers (<50 words) in a separate FAQs field and ensure mobile-first readability and E-E-A-T.

    **Inputs:**  
    Rows from `content_briefs` worksheet (where `Generated` = "No"), containing:
    - Keyword/Topic
    - Brief Content (Markdown with H1, introduction, H2 headings, link suggestions)
    - FAQs (JSON string with 5–7 question-answer pairs)
    - External Source Links
    - Content Summary

    **Instructions:**  

    1. **Find and Validate Brief:**  
    - Use `manage_sheet_data_tool` (action="find_row_by_key", worksheet_name="content_briefs", key_column="Generated", key_value="No") to find rows that are not yet generated.
    - Select the first matching row; if none exist, return:  
        { "status": "error", "message": "No ungenerated briefs found in content_briefs.", "errors": [], "warnings": [] }
    - Extract the following fields from the found row: Keyword/Topic, Brief Content, FAQs, External Source Links, and Content Summary.
    - Validate that essential fields (Keyword/Topic, Brief Content, and FAQs) are present and not empty; if any essential field is missing or empty, return an error.
    - Validate Brief Content structure (should include H1, introduction, 4–6 H2 headings) and FAQs format (5–7 question-answer pairs in JSON); if invalid, return:  
        { "status": "error", "message": "Invalid brief: missing H1, sections, or FAQs.", "errors": [], "warnings": [] }
    - Continue to Step 2 (Retrieve Author Context) only if valid data was extracted and validated.  

    2. **Retrieve Author Context:**  
    - Call `get_author_context_tool` to obtain JSON or Markdown with:  
        - `tone`: e.g., "professional, approachable"  
        - `emojis`: e.g., ["🚀", "✅"]  
        - `banned_words`: e.g., ["game-changer", "synergy"]  
        - `writing_style`: e.g., "technical guide with practical examples"
        - `expertise`: e.g., "AI development, content strategy"
    - Retry up to 3 times with 5-second delays; if unavailable, use default: "professional, approachable, no jargon" and include:  
        { "warnings": ["get_author_context_tool unavailable; used default tone"] }  
    - Use the author's writing style and expertise to create content that sounds like it's written by a real person with genuine knowledge and experience. Write in first person singular ("I") to create a personal connection with the reader.

    3. **Generate Blog Post:**  
    - Generate a 1500–2500-word blog post in Markdown format, aligned with user intent and author context:  
    - **Title (H1)**: Include the primary keyword and make the title engaging and intent-driven. Create a compelling, curiosity-driven title that captures interest without being clickbait—it should invite a click while accurately reflecting what the reader will get on the page. The title must set a clear, deliverable expectation and the generated content must fulfil that promise. Important: This title will be used as the H1 heading for the page - do not include the title/H1 again in the generated content. The generated content should start directly with the introduction H2, not repeat the title as an H1.
    - **Summary (meta description)**: Also produce a short, SEO-friendly summary (50–160 characters) that includes the primary keyword, accurately summarizes the page, and can be used as the meta description in search results. This summary should be concise, compelling, and non-clickbait.
        - **Introduction H2**: 150–200 words, front-loading primary keyword, conversational tone. Start with a strong hook that grabs attention immediately - this could be a thought-provoking question, surprising statistic, relatable scenario, or bold statement. Address user intent clearly and set expectations for what the reader will learn.
        - **Main Sections**: Use 4–6 H2 headings from Brief Content, expanding each into concise, informative content:  
        - Cover subtopics comprehensively to form a topic cluster (e.g., "Nespresso Features," "Budget Options").  
        - Use conversational language with a personal touch (e.g., "You know how frustrating it is when your coffee maker takes forever? Let me show you some better options.").  
        - Keep paragraphs very short - 2-3 sentences each maximum for better readability. Each paragraph should focus on a single idea or concept.  
        - Include 1–2 questions per section sourced via `tavily_search_tool` (query: "[Keyword/Topic] questions", max_results=5) or `tavily_extract_tool` from External Source Links:  
            - Example: "Why do some coffee makers brew faster?" (Direct answer: <50 words, e.g., "Fast-brew coffee makers use high-pressure systems."; followed by detailed explanation).  
        - Integrate secondary keywords naturally (2–3 uses each, e.g., "compact coffee maker").  
    - Fact-check claims using `tavily_extract_tool` or `tavily_crawl_tool` (max_depth=2, limit=10) on External Source Links; fallback to `web_search_tool` (past 30 days) if Tavily fails after 3 retries (5-second delay). Note unverified claims (e.g., "Claim about brewing speed unverified").  

    4. **Writing and Style Requirements**:  
    - **Style**:  
        - Use first person ("I") and personal pronouns ("you") for connection
        - Never use colons in headings (e.g., NOT "The Tangible Benefits: What Consistency Delivers" but "The Tangible Benefits of Consistency")  
        - Never use semicolons in headings or titles
        - Create meaningful, full headings without colons, semicolons, or special formatting - headings must clearly indicate the section content and provide value to the reader
        - Avoid AI-generated sounding phrases like "In today's digital landscape" or "Let's dive deeper into this topic"  
        - Write naturally as if a human expert is explaining the topic  
        - Never use em dashes (—) or other special punctuation that makes content look AI-generated  
        - Use contractions (don't, can't, it's) to sound more conversational  
        - Check content against banned words list from `get_author_context_tool`  
        - Focus on providing value and answering user questions directly  
    - **Structure**:  
        - Keep paragraphs very short with 2-3 sentences maximum - this is critical for readability
        - Each paragraph should focus on a single idea or point
        - Use bullet points and numbered lists extensively for better readability and structure:
            - When presenting multiple benefits, features, or steps (use bulleted lists)
            - When providing sequential instructions or ranked items (use numbered lists)
            - When comparing different options or approaches (use tables or bulleted lists)
            - When listing tips, best practices, or recommendations (use bulleted lists)
            - When breaking down complex concepts into digestible points
            - When summarizing key takeaways or action items
        - CRITICAL: Do not repeat the blog post title in the generated content column - start directly with the introduction H2
        - CRITICAL: The Generated Content column in the worksheet must NOT contain any H1 heading - only start with H2 and subsequent heading levels
        - Remove any placeholder text like "[50-100 words]" or "[100-150 words]" from the content
        - NEVER add a "Related Posts" heading or section at the end of the content
    - **Optimization**:
        - Answer "People Also Ask" questions directly
        - Structure content to directly answer "People Also Ask" questions that appear in search results  
        - Ensure the first paragraph contains a clear, direct answer to the main keyword/topic question  
        - Include specific numbers, facts, and actionable advice that search engines can easily extract  
        - Maximize structured data opportunities with bullet points and numbered lists
        - Optimize for featured snippets by including clear, concise answers to common questions  
        - Focus on direct, concise answers to user questions throughout the content
    - **Link Integration**:  
        - Internal links: Use descriptive anchor text (e.g., "learn more about social media automation" not "click here")  
        - External links: Use descriptive anchor text (e.g., "according to industry research" not "source")  
        - Never use generic link text like "click here," "read more," or "link"
        - Integrate links naturally within the content, not in a separate "Related Posts" section

    5. **Evaluate and Iterate:**  
    - Use `get_evaluation_feedback` to evaluate content:  
        - Readability (30%): Short sentences, conversational tone, mobile-friendly (Flesch-Kincaid 60–70, <5 grammar errors).  
        - Relevance (30%): Aligns with user intent, keywords, and subtopics; all claims verified.  
        - SEO (20%): Word count (1500–2500), FAQs (5–7 questions), keyword usage (2–3 per keyword), natural link integration.  
        - High Value to User (20%): Answers user queries comprehensively, with natural links enhancing context.  
    - Calculate score: (0.3 * readability_score + 0.3 * relevance_score + 0.2 * seo_score + 0.2 * value_score) * 100.  
    - If score < 90% and iteration count < 3, revise content based on feedback (e.g., "Simplify paragraph 3," "Add keyword 'smart brewing'," "Improve link placement in section 2").  
    - Track the highest-scored content and its score across iterations.  
    - If score ≥ 90% or after 3 iterations, proceed with the highest-scored content.  
    - Retry `get_evaluation_feedback` up to 3 times with 5-second delays if it fails.  

    6. **Save Generated Content:**  
    - Use `manage_sheet_data_tool` (action="append_row", worksheet_name="generated_posts") to save:  
    - Title  (H1 heading with primary keyword, engaging and intent-driven, with no colons or semicolons)
    - Generated Content (highest-scored Markdown string with integrated links) - IMPORTANT: This should be ONLY the content, NOT including the FAQs
    - FAQs (JSON string) - IMPORTANT: This should be a separate JSON string containing the FAQs, not combined with the content
    - Quality Score (integer in JSON response, but must be converted to string when calling `manage_sheet_data_tool`)
    - Summary (SEO-friendly meta description, 50–160 characters, must include the primary keyword and accurately summarize the page)
        - Approve/Disapprove ("Approved")  
        - Published ("No")  
    - The generated_posts worksheet has the following columns in order: Title, Generated Content, FAQs, Quality Score, Summary, Approve/Disapprove, Published
    - Example tool call:  
        {
        "worksheet_name": "generated_posts",
        "action": "append_row",
        "row_values": ["best coffee maker 2025", "# Best Coffee Makers 2025...
    ## Introduction...
    Nespresso excels, per [Coffee Review](https://coffeereview.com)...", "[{"question": "Can a coffee maker save time?", "answer": "Yes, models like Nespresso..."}]", "92", "Generated", "", "No"]
            }
    - IMPORTANT: All values in `row_values` must be strings, including numbers like Quality Score. Convert integers to strings (e.g., `92` should be `"92"`).
    - IMPORTANT: When using the Quality Score from the content evaluation agent's response, make sure to convert it from integer to string before adding to `row_values`. For example, if the evaluation agent returns `"Quality Score": 92`, convert it to `"92"` when constructing the `row_values` array.
    - Retry up to 3 times with 5-second delays; if it fails, include:  
        { "errors": ["Failed to save to generated_posts after 3 attempts"] }

    7. **Update `content_briefs` Row:**  
    - Use `manage_sheet_data_tool` (action="get_range", worksheet_name="content_briefs", cell_range="1:1") to identify the `Generated` column index.  
    - Use `manage_sheet_data_tool` (action="update_cell", worksheet_name="content_briefs", row_index=[row_index], col_index=[Generated_column_index], data="Yes").
    - When calling `update_cell`, the `data` parameter should be a simple string value, not a nested list. For example: `data="Yes"` not `data=[["Yes"]]`.  
    - Retry up to 3 times with 5-second delays; if it fails, include:  
        { "warnings": ["Failed to update Generated column in content_briefs after 3 attempts"] }  

    8. **Persistence and Fallbacks:**  
    - Retry all tools up to 3 times with 5-second delays.  
    - Use fallbacks if Tavily tools fail.  
    - If `get_evaluation_feedback` tool is unavailable or fails after retries, skip evaluation and proceed directly to save the generated content to the `generated_posts` worksheet and update the `Generated` column in `content_briefs` worksheet to "Yes".  

    **Tools:**  
    - `manage_sheet_data_tool`: Read from `content_briefs`, write to `generated_posts`, update `Generated` column.  
    - `get_author_context_tool`: Retrieve author context with tone, emojis, banned words.  
    - `tavily_search_tool`: Source user questions (1 credit/query).  
    - `tavily_extract_tool`: Fact-check content (1 credit/5 URLs).  
    - `tavily_crawl_tool`: Deep content exploration (1 credit/5 URLs).  
    - `web_search_tool` (fallback): Web content for fact-checking.  
    - `get_evaluation_feedback`: Evaluate content quality (readability, relevance, SEO, user value).  
    - `fetch_internal_links_tool`: Fetch internal links for natural integration (LIMITED TO 3 USES PER RUN - use strategically).
    **Output (JSON in Markdown):**  

    {
    "status": "success",
    "Title": "best coffee maker 2025",
    "Generated Content": "## Introduction
    Ever wondered which coffee maker brews the perfect cup for your busy mornings? [150–200 words]
    ## Nespresso Features
    Why do some coffee makers brew faster? Nespresso excels, per [Coffee Review](https://coffeereview.com)... [300–500 words, link to /blog/ai-tips]
    ",
    "FAQs": "[{"question": "Can a coffee maker save you time?", "answer": "Yes, models like Nespresso automate brewing. [100–150 words]"}, {"question": "How do you choose a coffee maker for small spaces?", "answer": "Look for compact models. [100–150 words]"}]",
    "Quality Score": "92",
    "Summary": "Summary of the content of blog post in 2-4 sentences",
    "Approve/Disapprove": "Approved" (By default its approved user can change it later),
    "Published": "No",
    "errors": [],
    "warnings": []
    }
    """,
    tools=[manage_sheet_data_tool, get_author_context_tool, web_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, fetch_internal_links_tool, content_evaluation_agent.as_tool(tool_name="get_evaluation_feedback", tool_description="Get evaluation feedback for the content to use the feedback for improvements")],
    handoff_description="Use the given brief to create a high quality seo friendly Blog content, and use evaluation tools for feedback and improve the content using it.",
    hooks=MyAgentHooks(),
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
    model_settings=ModelSettings(temperature=0.7),
)

brief_agent = Agent(
    name="Brief Agent",
    instructions="""
    **Role:** You are an SEO expert creating content briefs from research data.
    
    **Workflow:**
    1. Find unprocessed research data:
       - Use `manage_sheet_data_tool` with action="find_row_by_key", worksheet_name="research_data", key_column="Generated", key_value="No"
       - If no rows found, return error JSON: {"status": "error", "message": "No ungenerated rows found in research_data.", "errors": [], "warnings": []}
    
    2. Get author context (retry up to 3 times if needed):
       - Call `get_author_context_tool` to get tone, emojis, banned_words
       - If unavailable, use default: "professional, approachable, no jargon"
    
     3. Create content brief with these sections to be saved in the Brief Content column:
     - H1 title with primary keyword (curiosity-driven and hooky but not clickbait; must set an accurate, deliverable expectation that the brief enables the writer to fulfil)
         - 100-150 word intro with keyword, addressing user intent
         - 4-6 H2 headings with 50-100 word descriptions
         - Short summary/meta description (50-160 characters, SEO-friendly) to be used as the page meta description
         - Each section should include 1-2 conversational questions
         - Suggest natural link placements throughout (format as [Link Text](URL) for later integration)
         - Follow writing guidelines: no colons in headings, short paragraphs, natural tone
    
    4. Generate 5-7 FAQs in JSON format:
       - Use `tavily_search_tool` with query "People Also Ask [Keyword/Topic]" or `tavily_extract_tool` on source URLs
       - Questions should be conversational and answers <50 words for AI Overviews
       - Validate answers using `tavily_extract_tool` or `tavily_crawl_tool`
    
    5. Verify source titles using `tavily_extract_tool` on Source URLs
       - Format: "Title: URL"
    
    6. Save to `content_briefs` worksheet using `manage_sheet_data_tool` with action="append_row" to save the following in order:
       - Keyword/Topic (from the research_data row)
       - Brief Content (Markdown with H1, introduction, H2 headings, link suggestions)
       - FAQs (JSON string)
       - External Source Links (comma-separated URLs with titles)
    - Content Summary (retained from research_data row) — must be a 50–160 character SEO-friendly meta description
       - Generated ("No")
    
    7. Update the original row in `research_data` by setting Generated to "Yes"
    
    **Always return complete JSON with:** status, Keyword/Topic, Brief Content, FAQs, External Source Links, Content Summary, errors, warnings
    """,
    tools=[web_search_tool, tavily_search_tool, tavily_extract_tool, tavily_crawl_tool, manage_sheet_data_tool, get_author_context_tool],
    hooks=MyAgentHooks(),
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
    model_settings=ModelSettings(temperature=0.8),
)
