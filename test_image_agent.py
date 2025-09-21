#!/usr/bin/env python3
"""
Test script for the simplified image agent
"""
import sys
import os
import asyncio
import json

# Add the project directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from blog_agent.image_agent import select_blog_image

async def test_image_agent():
    """Test the image agent with a sample blog post"""
    
    title = "Brand Consistency in Social Media"
    content = """
    Maintaining brand consistency across all social media platforms is crucial for building trust and recognition with your audience. 
    When your visual identity, tone of voice, and messaging remain consistent, your followers know exactly what to expect from your content.
    
    Key elements of brand consistency include:
    - Using the same color palette and fonts
    - Maintaining a consistent posting schedule
    - Keeping a uniform tone in captions and comments
    - Using similar visual styles in images and videos
    
    Benefits of brand consistency:
    1. Builds trust and credibility
    2. Increases brand recognition
    3. Improves audience engagement
    4. Enhances professional image
    
    In this post, we'll explore practical strategies for maintaining brand consistency across all your social media channels.
    """
    
    print("Testing image agent...")
    print(f"Title: {title}")
    print(f"Content preview: {content[:100]}...")
    print("\n" + "="*50 + "\n")
    
    try:
        result = await select_blog_image(title, content)
        print("Image agent result:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error testing image agent: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_image_agent())