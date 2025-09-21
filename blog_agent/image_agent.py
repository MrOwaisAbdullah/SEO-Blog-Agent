"""
Simplified Image Agent for ContentSpark AI
Uses OpenAI Agents SDK to intelligently select or generate images with quality evaluation
"""
import os
import json
import time
import logging
import re
from typing import Dict, Any, Optional, List
from agents import Agent, ModelSettings, function_tool
from tools.tools import get_stock_image_tool, generate_image_tool
import requests
from io import BytesIO
from PIL import Image
import base64
from blog_agent.custom_runner import FallbackAgentRunner

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
    4. **Check for Prohibited Content**: Ensure no logos, brands, or poorly rendered faces are present
    5. **Provide Detailed Feedback**: Give specific reasons for your score and approval decision
    
    ## Evaluation Criteria:
    - **Relevance (30% weight)**: How well does the image represent the blog topic?
    - **Visual Quality (25% weight)**: Resolution, clarity, composition, and aesthetics
    - **Professionalism (20% weight)**: Does it look professional and appropriate for a business blog?
    - **Content Safety (15% weight)**: No logos, brands, or inappropriate content
    - **Technical Quality (10% weight)**: Proper rendering without artifacts
    
    ## Prohibited Content Check:
    - Logos or brand names
    - Poorly rendered human faces (distorted features, incorrect eyes/nose, unrealistic proportions)
    - Celebrity or specific person likenesses
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
    model=custom_runner.get_model_by_name("grok-4-fast-openrouter"),  # Using the specified model for advanced image analysis
    model_settings=ModelSettings(temperature=0.3),  # Lower temperature for more consistent evaluations
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
    - Include style guidance (hyper-realistic, 3D render, futuristic, etc.)
    - Add specific visual elements that represent the topic
    - Avoid mentioning specific companies or people unless they're the main focus
    - Explicitly specify high-quality visual styles like:
      * "hyper-realistic, game-quality render"
      * "professional 3D visualization"
      * "futuristic digital art"
      * "clean, modern illustration"
    - **Never include**: faces, people, humans, logos, brands, companies
    - **Avoid**: Pixar-style, cartoonish, or character-based representations
    
    ## Example Good Prompts:
    - "Hyper-realistic, game-quality render of AI analyzing data with futuristic interface elements"
    - "Professional 3D visualization of brand consistency concept with unified visual elements"
    - "Futuristic digital art of business strategy with workflow optimization visualization"
    - "Clean, modern illustration of SEO optimization with search engine interface elements"
    
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
    model=custom_runner.get_model_by_name("gemini-2.5-flash"),
    model_settings=ModelSettings(temperature=0.7),
)

if __name__ == "__main__":
    # Example usage
    import asyncio
    
    async def test_image_selection():
        result = await Runner.run(image_selection_agent, "Brand Consistency in Social Media",
            "Brand Consistency in Social Media",
            "Learn how to maintain a consistent brand voice across all social media platforms..."
        )
        print(json.dumps(result.final_output, indent=2))
        
    asyncio.run(test_image_selection())