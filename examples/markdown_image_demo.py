#!/usr/bin/env python3
"""
Demo script showing how to use markdown with images in Sanity posts.
"""

import os
import sys
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from lib.markdown_parser import markdown_to_sanity_blocks
from lib.sanity_adapter import SanityAdapter

def demo_markdown_image_parsing():
    """Demonstrate markdown image parsing."""
    print("=== Markdown Image Parsing Demo ===")
    
    # Sample markdown with images
    markdown_content = """# How to Use Images in Your Blog Posts

Adding images to your blog posts can greatly enhance the reader experience.

![Beautiful landscape](https://example.com/landscape.jpg "A beautiful landscape photo")

## Best Practices for Blog Images

1. Always include descriptive alt text
2. Use appropriate image dimensions
3. Compress images for faster loading

![Infographic about blogging tips](/local/images/blogging-tips.png)

With these tips, your blog posts will look professional and engaging!

![Chart showing engagement metrics](https://example.com/chart.png "Engagement metrics chart")
"""
    
    print("Input markdown:")
    print(markdown_content)
    print("\n" + "="*50)
    
    # Parse to Sanity blocks
    blocks = markdown_to_sanity_blocks(markdown_content)
    
    print(f"Parsed into {len(blocks)} Sanity blocks:")
    
    # Show different block types
    block_counts = {}
    for block in blocks:
        block_type = block.get('_type', 'unknown')
        block_counts[block_type] = block_counts.get(block_type, 0) + 1
        
        if block_type == 'image':
            print(f"\nImage Block:")
            print(f"  URL: {block.get('asset', {}).get('url', 'N/A')}")
            print(f"  Alt Text: {block.get('alt', 'N/A')}")
            if 'title' in block:
                print(f"  Title: {block['title']}")
        elif block_type == 'block':
            style = block.get('style', 'normal')
            if style in ['h1', 'h2']:
                text = ''.join([child.get('text', '') for child in block.get('children', [])])
                print(f"\n{style.upper()}: {text}")
    
    print(f"\nBlock Summary: {block_counts}")

def demo_sanity_post_with_images():
    """Demonstrate creating a Sanity post with images."""
    print("\n=== Sanity Post Creation Demo ===")
    
    # Check if we have Sanity credentials
    required_vars = ['SANITY_PROJECT_ID', 'SANITY_API_TOKEN', 'SANITY_DEFAULT_AUTHOR_ID']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"Note: Missing environment variables {missing_vars}")
        print("Skipping Sanity post creation demo.")
        print("To run this demo, set the required environment variables.")
        return
    
    # Sample blog content with images
    blog_content = """# 5 Essential Tips for Content Creators

Creating engaging content is both an art and a science. Here are 5 essential tips that will help you stand out.

![Content creation workflow](https://example.com/workflow.png "Content creation workflow diagram")

## 1. Know Your Audience

Understanding your audience is the foundation of great content. Research their pain points, interests, and preferences.

## 2. Create a Content Calendar

Plan your content in advance using a calendar. This helps maintain consistency and reduces last-minute stress.

![Content calendar example](/local/images/calendar.png)

## 3. Focus on Quality Over Quantity

It's better to publish one high-quality piece than several mediocre ones.

## 4. Use Visual Elements

Images, charts, and infographics make your content more engaging and easier to understand.

## 5. Analyze and Iterate

Regularly review your content performance and make improvements based on data.

![Analytics dashboard](https://example.com/analytics.png "Content performance analytics")
"""
    
    print("Creating a blog post with embedded images...")
    
    try:
        # Initialize Sanity adapter
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET') or "production",
            token=os.environ['SANITY_API_TOKEN']
        )
        
        # Parse content to blocks
        content_blocks = markdown_to_sanity_blocks(blog_content)
        print(f"Content parsed into {len(content_blocks)} blocks")
        
        # Count image blocks
        image_count = sum(1 for block in content_blocks if block.get('_type') == 'image')
        print(f"Found {image_count} embedded images in content")
        
        print("Demo completed successfully!")
        print("In a real implementation, this would create a blog post in Sanity.")
        
    except Exception as e:
        print(f"Error in demo: {e}")

def main():
    """Run the demos."""
    print("Markdown Image Handling Demo")
    print("=" * 40)
    
    demo_markdown_image_parsing()
    demo_sanity_post_with_images()
    
    print("\n" + "=" * 40)
    print("Demo completed!")

if __name__ == "__main__":
    # Load environment variables if .env file exists
    env_file = project_root / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
    
    main()