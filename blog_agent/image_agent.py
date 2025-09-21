#!/usr/bin/env python3
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

logger = logging.getLogger(__name__)

@function_tool
def evaluate_image_quality_tool(image_path: str, title: str, content: str) -> Dict[str, Any]:
    """
    Evaluates image quality and relevance using multimodal models.
    
    Args:
        image_path: Path or URL to the image
        title: Blog post title
        content: Blog post content
        
    Returns:
        Dict with quality score and feedback
    """
    try:
        # Load the image
        if image_path.startswith('http'):
            logger.info(f"Loading image from URL: {image_path}")
            response = requests.get(image_path)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content))
        else:
            logger.info(f"Loading image from local path: {image_path}")
            image = Image.open(image_path)
            
        # Basic quality checks
        width, height = image.size
        aspect_ratio = width / height
        
        logger.info(f"Image dimensions: {width}x{height}, aspect ratio: {aspect_ratio:.2f}")
        
        # Quality assessment criteria
        issues = []
        if width < 800 or height < 600:
            issues.append("Low resolution")
            
        if aspect_ratio < 0.5 or aspect_ratio > 2.0:
            issues.append("Unusual aspect ratio")
            
        # Relevance check - extract key topics from content
        content_lower = (title + " " + content[:300]).lower()
        
        # Extract potential keywords from title and content
        words = content_lower.split()
        # Filter out common stop words
        stop_words = {"the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "is", "are", "was", "were", "be", "been", "have", "has", "had", "do", "does", "did", "will", "would", "could", "should", "may", "might", "must", "can", "this", "that", "these", "those", "a", "an", "as", "it", "its"}
        keywords = [word.strip('.,!?;:"') for word in words if word.strip('.,!?;:"') not in stop_words and len(word) > 2]
        
        # Check relevance based on content themes
        theme_indicators = {
            "technology": ["ai", "artificial", "intelligence", "machine", "learning", "automation", "software", "digital", "tech"],
            "business": ["company", "brand", "marketing", "strategy", "growth", "startup", "enterprise", "corporate"],
            "creative": ["design", "content", "creative", "visual", "art", "illustration", "graphic"],
            "social": ["social", "media", "platform", "post", "engagement", "audience"],
            "education": ["learn", "guide", "tutorial", "how-to", "tips", "best practices", "expert"],
            "productivity": ["efficiency", "productivity", "workflow", "tools", "process", "optimize"]
        }
        
        # Determine content themes
        content_themes = []
        for theme, indicators in theme_indicators.items():
            if any(indicator in content_lower for indicator in indicators):
                content_themes.append(theme)
        
        # Check for people/brands that should be avoided
        brand_person_indicators = ["person", "people", "user", "customer", "client", "human", "individual",
                                  "company", "brand", "product", "service", "ceo", "founder", "corporate"]
        contains_brand_person = any(indicator in content_lower for indicator in brand_person_indicators)
        
        # Calculate score (10 is perfect)
        base_score = 10.0
        if issues:
            base_score -= len(issues) * 2
            
        # If it's about a specific person/brand, penalize if we can't verify it's appropriate
        if contains_brand_person:
            base_score -= 1  # Small penalty
            
        approved = base_score >= 7.0
        
        feedback = "Quality assessment: "
        if issues:
            feedback += "; ".join(issues)
        else:
            feedback += "Good quality and relevance"
            
        if not approved:
            feedback += ". Consider alternatives."
            
        logger.info(f"Image evaluation - Score: {base_score}, Approved: {approved}, Feedback: {feedback}")
            
        return {
            "score": base_score,
            "feedback": feedback,
            "approved": approved,
            "resolution": f"{width}x{height}",
            "contains_brand_person": contains_brand_person,
            "themes": content_themes
        }
    except Exception as e:
        logger.error(f"Error evaluating image: {e}")
        return {
            "score": 5.0,
            "feedback": f"Evaluation error: {str(e)}",
            "approved": False,
            "resolution": "Unknown",
            "contains_brand_person": False,
            "themes": []
        }

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
    5. **Generate Images**: Use `generate_image_tool` to create AI images with your custom prompts
       - If generation fails, note the specific error and try alternatives
       - Try up to 3 different prompts if initial attempts fail
    6. **Evaluate Quality**: Use `evaluate_image_quality_tool` to assess images
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
      * Use visual metaphors
    - For technical topics:
      * Use clean, professional illustrations
      * Include relevant visual elements (charts, interfaces, etc.)
    - For creative topics:
      * Use vibrant, engaging visuals
      * Consider artistic styles
    
    ## Prompt Creation Guidelines:
    - Extract 3-5 key concepts from the title and content
    - Create prompts that visualize these concepts, not generic terms
    - Include style guidance (professional, clean, modern, etc.)
    - Add specific visual elements that represent the topic
    - Avoid mentioning specific companies or people unless they're the main focus
    
    ## Example Good Prompts:
    - "Professional illustration of AI analyzing social media content with data visualization elements"
    - "Clean, modern design showing brand consistency concept with unified visual elements"
    - "Business strategy meeting with digital workflow optimization visualization"
    
    ## Example Bad Prompts:
    - "A generic image about social media" (too vague)
    - "Social media automation" (doesn't visualize a concept)
    - "Person using computer" (too generic)
    
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
        evaluate_image_quality_tool
    ],
    model="gemini-2.0-flash",
    model_settings=ModelSettings(temperature=0.7),
)

async def select_blog_image(title: str, content: str) -> Dict[str, Any]:
    """
    Selects or generates a high-quality image for a blog post.
    
    Args:
        title: Blog post title
        content: Blog post content
        
    Returns:
        Dict with image information
    """
    try:
        logger.info(f"Selecting image for blog post: {title}")
        
        # Import the custom runner
        from blog_agent.custom_runner import FallbackAgentRunner
        custom_runner = FallbackAgentRunner()
        
        # Run the image selection agent
        prompt = f"""
        Select or generate an image for a blog post with:
        Title: {title}
        Content: {content[:500]}...
        """
        
        result = await custom_runner.run_with_fallback(
            image_selection_agent,
            prompt,
            max_retries=3,
            max_turns=30
        )
        
        # Extract the final output
        if hasattr(result, 'final_output'):
            final_output = result.final_output
            # If it's a string, try to parse it as JSON
            if isinstance(final_output, str):
                try:
                    import json
                    # Try to parse directly first
                    return json.loads(final_output)
                except json.JSONDecodeError:
                    try:
                        # Remove markdown code block wrappers if present
                        import re
                        cleaned_output = re.sub(r'^```(?:json)?\s*', '', final_output.strip(), flags=re.DOTALL)
                        cleaned_output = re.sub(r'\s*```$', '', cleaned_output, flags=re.DOTALL)
                        # Handle escaped quotes
                        cleaned_output = cleaned_output.replace('"', '"')
                        return json.loads(cleaned_output)
                    except (json.JSONDecodeError, Exception) as parse_error:
                        logger.error(f"JSON parsing error: {parse_error}")
                        return {"error": f"Failed to parse agent output: {str(parse_error)}", "raw_output": final_output}
            return final_output
        else:
            # If it's a string, try to parse it as JSON
            result_str = str(result)
            try:
                import json
                return json.loads(result_str)
            except json.JSONDecodeError:
                try:
                    # Remove markdown code block wrappers if present
                    import re
                    cleaned_output = re.sub(r'^```(?:json)?\s*', '', result_str.strip(), flags=re.DOTALL)
                    cleaned_output = re.sub(r'\s*```$', '', cleaned_output, flags=re.DOTALL)
                    # Handle escaped quotes
                    cleaned_output = cleaned_output.replace('"', '"')
                    return json.loads(cleaned_output)
                except (json.JSONDecodeError, Exception) as parse_error:
                    logger.error(f"JSON parsing error: {parse_error}")
                    return {"error": f"Failed to parse agent output: {str(parse_error)}", "raw_output": result_str}
            
    except Exception as e:
        logger.error(f"Error selecting blog image: {e}")
        return {
            "error": f"Failed to select image: {str(e)}",
            "image_url": None,
            "alt_text": f"{title} image",
            "source": "None",
            "evaluation_score": 0.0,
            "feedback": "Complete failure in image selection"
        }

@function_tool
async def get_blog_image_tool(title: str, content: str) -> Dict[str, Any]:
    """
    Tool to get a high-quality image for a blog post.
    
    Args:
        title: Blog post title
        content: Blog post content
        
    Returns:
        Dict with image information
    """
    return await select_blog_image(title, content)

if __name__ == "__main__":
    # Example usage
    import asyncio
    
    async def test_image_selection():
        result = await select_blog_image(
            "Brand Consistency in Social Media",
            "Learn how to maintain a consistent brand voice across all social media platforms..."
        )
        print(json.dumps(result, indent=2))
        
    asyncio.run(test_image_selection())