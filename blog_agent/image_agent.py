"""
Simplified Image Agent for ContentSpark AI
Uses OpenAI Agents SDK to intelligently select or generate images with quality evaluation
"""
import os
import json
import time
import logging
import re
import asyncio
from typing import Dict, Any, Optional, List
from agents import Agent, ModelSettings, function_tool
from tools.tools import get_stock_image_tool, generate_image_tool
from tools.screenshot_tool import capture_screenshot_tool
import requests
from io import BytesIO
from PIL import Image
import base64
from blog_agent.custom_runner import FallbackAgentRunner
from blog_agent.hooks import MyAgentHooks
from lib.run_result_utils import run_looks_failed

# Create a custom runner instance to access the get_model_by_name method
custom_runner = FallbackAgentRunner()

logger = logging.getLogger(__name__)

# Create the Image Quality Evaluation Agent
image_quality_evaluation_agent = Agent(
    name="Image Quality Evaluation Agent",
    instructions="""
    You are an expert at evaluating image quality and relevance for blog posts.
    
    ## Process:
    1. **Analyze Image Content**: Examine the visual elements, composition, and quality of the image
    2. **Assess Relevance**: Determine how well the image matches the blog post topic
    3. **Evaluate Technical Quality**: Check resolution, clarity, and visual appeal
    4. **Check for Prohibited Content**: Ensure no poorly rendered faces or objects are present
    5. **Provide Detailed Feedback**: Give specific reasons for your score and approval decision
    
    ## Evaluation Criteria:
    - **Relevance (30% weight)**: How well does the image represent the blog topic?
    - **Visual Quality (25% weight)**: Resolution, clarity, composition, and aesthetics
    - **Professionalism (20% weight)**: Does it look professional and appropriate for a business blog?
    - **Content Safety (15% weight)**: No inappropriate content
    - **Technical Quality (10% weight)**: Proper rendering without artifacts
    
    ## Prohibited Content Check:
    - Poorly rendered human faces (distorted features, incorrect eyes/nose, unrealistic proportions)
    - Trademarked characters or products
    - Inappropriate or offensive content
    - Watermarks or text overlays
    
    ## Face Detection Guidelines:
    When evaluating images for faces:
    - Look for realistic human facial features (eyes, nose, mouth)
    - Check for proper proportions and symmetry
    - Identify distorted or unrealistic facial renderings
    - Note if faces appear pixelated, blurry, or poorly constructed
    
    ## Scoring Guidelines:
    - **9-10**: Exceptional - Perfect match, high quality, professional, highly engaging
    - **7-8**: Good - Solid match, good quality, professional appearance
    - **5-6**: Acceptable - Adequate match, some quality issues, generally acceptable
    - **1-4**: Poor - Poor match, low quality, not recommended
    
    ## Image Analysis Process:
    1. First examine the image carefully
    2. Consider the blog title and content to assess relevance
    3. Evaluate technical aspects like resolution and clarity
    4. Check if the image is professional and appropriate
    5. Verify no prohibited content is present
    6. Provide a score and detailed feedback
    
    ## Output Format:
    Return a JSON object with:
    {
      "score": numerical score (0-10),
      "feedback": "Detailed explanation of the evaluation",
      "approved": boolean (true if score >= 7),
      "resolution": "Image dimensions (e.g., '1920x1080')",
      "technical_issues": ["list of technical issues found, if any"],
      "relevance_feedback": "How well the image matches the content",
      "prohibited_content": ["list of prohibited content found, if any"]
    }
    
    IMPORTANT: Always return valid JSON in a code block format like:
    ```json
    {
      "score": 8.5,
      "feedback": "...",
      "approved": true,
      "resolution": "1920x1080",
      "technical_issues": [],
      "relevance_feedback": "...",
      "prohibited_content": []
    }
    ```
    """,
    # qwen-2.5-openrouter (qwen/qwen2.5-vl-32b-instruct:free) was removed from
    # OpenRouter entirely -- not just its free tier, the model ID no longer
    # exists at all. Gemini 2.5 Flash is multimodal (vision-capable), already
    # free on this project's tier, and already configured elsewhere in this
    # codebase, so it's a zero-new-cost, zero-new-dependency replacement.
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
    model_settings=ModelSettings(temperature=0.3),  # Lower temperature for more consistent evaluations
    hooks=MyAgentHooks(),
)


# Create the Contextual Image Insertion Agent
contextual_image_insertion_agent = Agent(
    name="Contextual Image Insertion Agent",
    instructions="""
    You are an expert at identifying optimal positions for contextual images in blog content.
    
    ## INPUT FORMAT:
    You will receive blog content to enhance with contextual images. The content may come with additional metadata in the following format:
    === POST_DATA_START ===
    KEYWORD_TOPIC: [value]
    TITLE: [value]
    SUMMARY: [value]
    CONTENT_WITH_LINKS: [Main content that needs images - this is what you'll modify]
    CATEGORIES: [value]
    IMAGE_URL: [value]
    ALT_TEXT: [value]
    SLUG: [value]
    INTERNAL_LINKS_MD: [value]
    EXTERNAL_LINKS_MD: [value]
    FAQS: [value]
    SOURCE_KEYWORD_TOPIC: [value]
    === POST_DATA_END ===
    
    OR you may receive just the content directly.
    
    ## Process:
    1. **Analyze Content Structure**: Examine the blog content to identify major sections and natural breakpoints
    2. **Identify Image Opportunities**: Find positions where images would enhance understanding or engagement
    3. **Extract Key Concepts**: For each opportunity, extract 3-5 key concepts from surrounding content
    4. **Generate Image Queries**: Create specific queries for stock image services based on key concepts
    5. **Fetch Real Images**: Use `get_stock_image_tool` to find actual stock images (NOT example URLs)
    6. **Evaluate Image Relevance**: Use `evaluate_image_quality` to assess if found images are relevant and of high quality
    7. **Insert Images Strategically**: Place approved images at optimal positions in the content using PROPER MARKDOWN SYNTAX
    
    ## Content Analysis Guidelines:
    - Look for H2 headings as natural section breaks
    - Identify content clusters of 3-5 paragraphs that discuss related topics
    - Find topic transitions where visual reinforcement would help
    - Consider complex concepts that benefit from visual explanation
    
    ## Image Opportunity Criteria:
    - **High Value**: Content that is abstract, technical, or difficult to visualize
    - **Natural Breaks**: Between major sections or after introductory content
    - **Complex Topics**: Concepts that users might struggle to understand without visuals
    - **Engagement Boosters**: Points where an image could increase reader interest
    
    ## Key Concept Extraction:
    For each image opportunity, extract 3-5 key concepts:
    - Focus on nouns and descriptive adjectives
    - Avoid generic terms like "strategy" or "tips"
    - Include specific actions, objects, or scenarios
    - Capture the essence of the surrounding content
    
    ## Image Query Creation:
    Create specific, descriptive queries for stock image services:
    - Combine 3-5 key concepts into coherent search terms
    - Use concrete, visual language rather than abstract concepts
    - Include modifiers like "professional," "modern," or "business"
    - Avoid overly restrictive terms that might yield no results
    
    ## Real Screenshots Instead of Stock Photos (when it genuinely applies):
    If the surrounding content is specifically about a real, named tool/product/dashboard --
    not a general concept -- a genuine screenshot of that tool is more trustworthy than a stock
    photo, and you should prefer it when you can.
    - **HARD REQUIREMENT**: only call `capture_screenshot_tool` with a URL that appears
      VERBATIM in this input's `EXTERNAL_LINKS_MD` field (already-verified real sources from
      earlier in the pipeline). Never construct, guess, or recall a URL from your own training
      data -- an invented-but-plausible URL is worse than no screenshot, same rule as internal
      links elsewhere in this pipeline.
    - If no URL in `EXTERNAL_LINKS_MD` is a genuine match for what this section discusses, do
      not force it -- fall back to the normal stock-photo process below.
    - `capture_screenshot_tool` can only see a logged-out, public view of a page -- do not use
      it for anything that clearly requires being logged in (a personal dashboard, an admin
      panel). If it returns an `error`, treat that exactly like a failed stock-image fetch: log
      it and fall back to `get_stock_image_tool` for that section instead of skipping the image
      entirely.
    - Still run any screenshot through `evaluate_image_quality` like every other image before
      inserting it.

    ## Image Fetching Process:
    1. **Create Search Query**: Formulate a specific search term from the key concepts
    2. **Call get_stock_image_tool**: Use the tool with your search query to find a real stock image
    3. **Handle Results**: Extract the actual image URL and alt text from the tool response
    4. **Retry Logic**: If the first attempt fails, try alternative search terms
    5. **IMPORTANT**: NEVER use example URLs like "https://example.com/image-url.jpg"

    ## Image Evaluation Process:
    1. **Call evaluate_image_quality**: Pass the image URL to assess relevance and quality
    2. **Check Score**: Only insert images with a score of 7.0 or higher
    3. **Review Feedback**: Consider the evaluation feedback for placement decisions
    
    ## Insertion Guidelines:
    - Insert a maximum of 2 images per blog post
    - Place images close to relevant content, not arbitrarily
    - Ensure alt text accurately describes the image content
    - Use descriptive filenames that reflect image content
    - Maintain content flow and readability
    
    ## CRITICAL: Proper Image Insertion Format
    You MUST insert images using the correct markdown syntax:
    ```markdown
    ![Descriptive alt text](ACTUAL_IMAGE_URL "Optional title")
    ```
    
    ## Common Mistakes to Avoid:
    - DO NOT insert plain text like: [Person wearing smart glasses]
    - DO NOT insert just the alt text without the image syntax
    - DO NOT use placeholder/example URLs
    - DO NOT forget the exclamation mark (!) at the beginning
    
    ## Correct Image Insertion Examples:
    ```markdown
    ![Professional business team collaborating](https://images.pexels.com/photos/3184417/pexels-photo-3184417.jpeg "Business collaboration")
    
    ![Person wearing smart glasses navigating a city](https://images.pexels.com/photos/1234567/pexels-photo-1234567.jpeg "Augmented reality navigation")
    ```
    
    ## Incorrect Image Insertion Examples (AVOID THESE):
    ```markdown
    [Person wearing smart glasses navigating a city with augmented reality overlays]  # WRONG - plain text
    
    Person wearing smart glasses navigating a city  # WRONG - no image syntax
    
    ![Person wearing smart glasses](https://example.com/image-url.jpg)  # WRONG - example URL
    ```
    
    ## CRITICAL OUTPUT REQUIREMENTS:
    After inserting images, you MUST return the data in the SAME FORMAT in which you received it:
    
    If you received data starting with === POST_DATA_START ===, then return:
    ```text
    === POST_DATA_START ===
    KEYWORD_TOPIC: [original value or modified as needed]
    TITLE: [original value or modified as needed]
    SUMMARY: [original value or modified as needed]
    CONTENT_WITH_LINKS: [this is the content you modified by inserting images]
    CATEGORIES: [original value or modified as needed]
    IMAGE_URL: [original value or modified as needed]
    ALT_TEXT: [original value or modified as needed]
    SLUG: [original value or modified as needed]
    INTERNAL_LINKS_MD: [original value or modified as needed]
    EXTERNAL_LINKS_MD: [original value or modified as needed]
    FAQS: [original value or modified as needed]
    SOURCE_KEYWORD_TOPIC: [original value or modified as needed]
    === POST_DATA_END ===
    ```
    
    If you received just the content, return just the modified content.
    
    ## Critical Requirements:
    - **IMPORTANT**: You MUST use `get_stock_image_tool` to find real images
    - **IMPORTANT**: Do NOT use placeholder/example URLs like "https://example.com/image-url.jpg"
    - **IMPORTANT**: Only insert images with quality scores of 7.0 or higher
    - **IMPORTANT**: Always extract the actual URL from the `get_stock_image_tool` response
    - **IMPORTANT**: Use proper markdown image syntax with exclamation mark (!)
    - **IMPORTANT**: Place each image on its own line with appropriate spacing
    - **CRITICAL**: Preserve ALL existing content formatting, links, and structure
    - **CRITICAL**: Do NOT modify existing markdown elements, only ADD new images
    - **CRITICAL**: If you received data with === POST_DATA_START === markers, return it with the same markers
    - **CRITICAL**: Only the CONTENT_WITH_LINKS field should be modified with inserted images
    
    ## Example Good Insertion in Context:
    ```markdown
    ## Brand Consistency Principles
    
    Maintaining a consistent brand voice across all platforms is crucial for building trust with your audience.
    
    ![Marketing team working together](https://images.pexels.com/photos/3183197/pexels-photo-3183197.jpeg "Team collaboration in marketing")
    
    ### Visual Identity Alignment
    
    Your visual elements should reflect your brand's core values and messaging.
    ```
    
    ## Output Format (when data starts with === POST_DATA_START ===):
    Return the complete data structure with the same markers and fields, only modifying CONTENT_WITH_LINKS:
    ```text
    === POST_DATA_START ===
    KEYWORD_TOPIC: [value]
    TITLE: [value]
    SUMMARY: [value]
    CONTENT_WITH_LINKS: [Your modified content with inserted images]
    CATEGORIES: [value]
    IMAGE_URL: [value]
    ALT_TEXT: [value]
    SLUG: [value]
    INTERNAL_LINKS_MD: [value]
    EXTERNAL_LINKS_MD: [value]
    FAQS: [value]
    SOURCE_KEYWORD_TOPIC: [value]
    === POST_DATA_END ===
    ```
    
    ## Output Format (if data is just content):
    Return a JSON object with:
    {
      "content_with_images": "Original content with strategically placed images using PROPER MARKDOWN SYNTAX",
      "images_inserted": [
        {
          "position": "Description of where the image was inserted",
          "concepts": ["concept1", "concept2", "concept3"],
          "image_url": "ACTUAL_IMAGE_URL_FROM_TOOL",  # NOT an example URL
          "alt_text": "Real alt text from tool or generated",
          "score": 8.5,
          "feedback": "Reasoning behind the insertion"
        }
      ],
      "images_skipped": [
        {
          "concepts": ["concept1", "concept2"],
          "reason": "Why this image opportunity was skipped"
        }
      ]
    }
    
    When inserting images, use exact markdown format:
    ![Description](URL "Title")
    Do NOT modify existing links or other markdown elements in the content.
    Preserve all existing formatting, links, and structure in the content.
    """,
    tools=[
        get_stock_image_tool,
        capture_screenshot_tool,
        image_quality_evaluation_agent.as_tool(tool_name="evaluate_image_quality", tool_description="Evaluates image quality and relevance for contextual placement")
    ],
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
    model_settings=ModelSettings(temperature=0.7),
    hooks=MyAgentHooks(),
)


# Create the Image Selection Agent
image_selection_agent = Agent(
    name="Image Selection Agent",
    instructions="""
    You are an expert at selecting or generating high-quality, relevant images for blog posts.
    
    ## Process:
    1. **Analyze Content**: Understand the blog post's topic, tone, and audience
    2. **Extract Key Themes**: Identify the main themes and concepts from the title and content
    3. **Decide Strategy**: 
       - For general topics: Try AI generation first with contextually relevant prompts
       - For topics about specific people/brands: Be more careful, prefer abstract representations
    4. **Create Contextual Prompts**: Generate image prompts that are specific to the blog content
       - Avoid generic terms like "social media automation"
       - Focus on the actual topic (e.g., "AI content creation", "brand consistency", "SEO optimization")
       - Include relevant visual elements and style preferences
       - **IMPORTANT**: Never include faces, people, humans, logos, brands, or companies in prompts
    5. **Generate Images**: Use `generate_image_tool` to create AI images with your custom prompts
       - If generation fails, note the specific error and try alternatives
       - Try up to 3 different prompts if initial attempts fail
    6. **Evaluate Quality**: Use `image_quality_evaluation_agent` to assess images
    7. **Iterate**: If score < 7 or not approved:
       - Analyze feedback to understand issues
       - Create improved prompts based on feedback
       - Try different generation approaches
       - Limit total attempts to 3
    8. **Fallback**: If all generation attempts fail, use `get_stock_image_tool`
    
    ## Quality Standards:
    - Score ≥ 7.0 for approval
    - Minimum 800x600 resolution
    - Highly relevant to blog topic
    - Professional appearance
    - No text overlays
    - Appropriate for target audience
    
    ## Special Handling:
    - For content about specific people/brands:
      * Avoid realistic portraits or logos
      * Focus on abstract, conceptual representations
    - For technical topics:
      * Use clean, professional illustrations
      * Include relevant visual elements (charts, interfaces, etc.)
    - For creative topics:
      * Use vibrant, engaging visuals
    
    ## Prompt Creation Guidelines:
    - Extract 3-5 key concepts from the title and content
    - Create prompts that visualize these concepts, not generic terms
    - Include diverse style guidance to avoid repetitive blue/futuristic themes:
      * "vibrant, colorful digital painting" 
      * "warm, inviting photograph with natural lighting"
      * "bold graphic design with striking contrasts"
      * "artistic watercolor illustration with organic textures"
      * "dynamic action shot with dramatic angles"
      * "clean minimalist composition with ample white space"
      * "rich, saturated colors with cinematic lighting"
      * "hand-drawn sketch with expressive linework"
      * "retro-inspired design with vintage color palette"
      * "abstract geometric composition with modern elements"
    - Add specific visual elements that represent the topic
    - Avoid mentioning specific companies or people unless they're the main focus
    - Explicitly specify high-quality visual styles like:
      * "vibrant, high-saturation photograph with dynamic composition"
      * "expressive digital painting with rich textures and warm colors"
      * "professional product photography with studio lighting"
      * "artistic illustration with hand-drawn elements"
      * "cinematic scene with dramatic lighting and color grading"
      * "infographic-style visualization with clean lines"
      * "mixed media collage with layered textures"
      * "stylized 3D render with unique materials and lighting"
    - **Never include**: faces, people, humans, logos, brands, companies
    - **Avoid**: Generic terms like "futuristic", "digital", "technology", "blue tones"
    - **Encourage**: Unique color palettes, creative compositions, and artistic interpretations
    - **Focus on**: Emotional impact, visual storytelling, and brand-appropriate aesthetics
    
    ## Example Good Prompts:
    - "Vibrant, colorful digital painting illustrating AI analyzing data with dynamic interface elements and rich textures"
    - "Warm, inviting photograph of brand consistency concept with natural lighting and professional quality"
    - "Bold graphic design representing business strategy with striking contrasts and modern typography"
    - "Artistic watercolor illustration of SEO optimization with organic textures and flowing colors"
    
    ## Example Bad Prompts:
    - "A generic image about social media" (too vague)
    - "Social media automation" (doesn't visualize a concept)
    - "Person using computer" (includes people)
    - "Illustration of AI with human faces" (includes faces)
    
    ## Error Handling:
    - If AI generation fails, report the specific error
    - If stock image fallback is used, note this clearly
    - Always provide detailed feedback about why choices were made
    
    ## Output Format:
    Return a JSON object with:
    {
      "image_url": "URL to the selected image",
      "alt_text": "Descriptive alt text",
      "source": "AI Generated" or "Stock Photo",
      "evaluation_score": numerical score,
      "feedback": "Quality assessment feedback including any errors encountered"
    }
    
    IMPORTANT: Always return valid JSON in a code block format like:
    ```json
    {
      "image_url": "...",
      "alt_text": "...",
      "source": "...",
      "evaluation_score": ...,
      "feedback": "..."
    }
    ```
    """,
    tools=[
        generate_image_tool,
        get_stock_image_tool,
        image_quality_evaluation_agent.as_tool(tool_name="image_quality_evaluation_agent", tool_description="Evaluates image quality and relevance for blog posts")
    ],
    # Was pinned to "cohere" -- broken now that Cohere's been removed from
    # LLM_MODELS entirely (see custom_runner.py), which would have raised
    # ValueError at the first call. Switched to the same default used
    # elsewhere in this file rather than leaving it on a removed provider.
    model=custom_runner.get_model_by_name("gemini-flash-latest"),
    model_settings=ModelSettings(temperature=0.7),
    hooks=MyAgentHooks(),
)

async def run_image_selection_workflow(input_data: Any, max_retries: int = 2) -> Dict[str, Any]:
    """
    Executes the image selection workflow:
    1. Runs the Image Selection Agent to select or generate an image
    2. Returns the final result.
    """
    logger.info("Starting the image selection workflow...")

    max_turns = 50

    try:
        # Run Image Selection Agent with retry logic
        image_result = None
        for attempt in range(max_retries):
            try:
                logger.info(f"Running Image Selection Agent (attempt {attempt + 1}/{max_retries})...")
                image_result = await custom_runner.run_with_fallback(
                    image_selection_agent,
                    input_data,
                    max_retries=max_retries,
                    max_turns=max_turns
                )
                
                if not run_looks_failed(image_result):
                    logger.info("Image Selection Agent completed successfully")
                    break
                else:
                    logger.warning(f"Image Selection Agent failed on attempt {attempt + 1}: {str(image_result)}")
            except Exception as e:
                logger.warning(f"Image Selection Agent failed on attempt {attempt + 1} with exception: {str(e)}")

            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        if image_result is None or run_looks_failed(image_result):
            logger.error(f"Image Selection Agent failed after {max_retries} attempts")
            return {"status": "error", "error": f"Image Selection Agent failed after {max_retries} attempts: {str(image_result)}"}

        logger.info("Image selection workflow completed.")
        # Return the Image Selection Agent's output directly
        return {"status": "completed", "data": image_result}

    except Exception as e:
        logger.error(f"Error in image selection workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}

async def run_contextual_image_insertion_workflow(input_data: Any, max_retries: int = 2) -> Dict[str, Any]:
    """
    Executes the contextual image insertion workflow:
    1. Runs the Contextual Image Insertion Agent to insert images into content
    2. Returns the final result.
    """
    logger.info("Starting the contextual image insertion workflow...")

    max_turns = 50

    try:
        # Run Contextual Image Insertion Agent with retry logic
        insertion_result = None
        for attempt in range(max_retries):
            try:
                logger.info(f"Running Contextual Image Insertion Agent (attempt {attempt + 1}/{max_retries})...")
                insertion_result = await custom_runner.run_with_fallback(
                    contextual_image_insertion_agent,
                    input_data,
                    max_retries=max_retries,
                    max_turns=max_turns
                )
                
                if not run_looks_failed(insertion_result):
                    logger.info("Contextual Image Insertion Agent completed successfully")
                    break
                else:
                    logger.warning(f"Contextual Image Insertion Agent failed on attempt {attempt + 1}: {str(insertion_result)}")
            except Exception as e:
                logger.warning(f"Contextual Image Insertion Agent failed on attempt {attempt + 1} with exception: {str(e)}")

            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        if insertion_result is None or run_looks_failed(insertion_result):
            logger.error(f"Contextual Image Insertion Agent failed after {max_retries} attempts")
            return {"status": "error", "error": f"Contextual Image Insertion Agent failed after {max_retries} attempts: {str(insertion_result)}"}

        logger.info("Contextual image insertion workflow completed.")
        # Return the Contextual Image Insertion Agent's output directly
        return {"status": "completed", "data": insertion_result}

    except Exception as e:
        logger.error(f"Error in contextual image insertion workflow: {e}", exc_info=True)
        return {"status": "error", "error": f"Unexpected error in workflow: {str(e)}"}