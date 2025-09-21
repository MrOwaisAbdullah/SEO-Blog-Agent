#!/usr/bin/env python3
"""
Complete test of the markdown-to-Sanity workflow with embedded images.
"""

import os
import sys
import json
import tempfile
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from lib.markdown_parser import markdown_to_sanity_blocks
from lib.sanity_adapter import SanityAdapter

def test_full_workflow():
    """Test the complete workflow from markdown to Sanity with embedded images."""
    print("=== Testing Complete Workflow ===")
    
    # Check if we have Sanity credentials
    required_vars = ['SANITY_PROJECT_ID', 'SANITY_API_TOKEN', 'SANITY_DEFAULT_AUTHOR_ID']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"Note: Missing environment variables {missing_vars}")
        print("Skipping full workflow test.")
        return
    
    # Create test markdown with embedded images
    test_markdown = """# Complete Workflow Test

This post tests the complete workflow with embedded images.

![First test image](https://httpbin.org/image/jpeg)

Content between images.

![Second test image with title](https://httpbin.org/image/png "This is the title")

More content at the end."""

    print("Input markdown:")
    print(test_markdown)
    
    # Parse to Sanity blocks
    print("\n1. Parsing markdown to Sanity blocks...")
    blocks = markdown_to_sanity_blocks(test_markdown)
    print(f"   Generated {len(blocks)} blocks")
    
    # Show image blocks
    image_blocks = [block for block in blocks if block.get('_type') == 'image']
    print(f"   Found {len(image_blocks)} embedded images:")
    for i, block in enumerate(image_blocks, 1):
        print(f"     Image {i}: {block.get('asset', {}).get('url', 'No URL')}")
    
    # Create a simple test image for the main image
    print("\n2. Creating test main image...")
    try:
        from PIL import Image, ImageDraw
        # Create a simple test image
        img = Image.new('RGB', (200, 200), color=(73, 109, 137))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "Main Image", fill=(255, 255, 0))
        
        # Save to temporary file
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        img.save(temp_file.name)
        main_image_path = temp_file.name
        temp_file.close()
        print(f"   Created main image: {main_image_path}")
        
    except Exception as e:
        print(f"   Error creating main image: {e}")
        return
    
    # Initialize Sanity adapter
    print("\n3. Initializing Sanity adapter...")
    try:
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET', 'production'),
            token=os.environ['SANITY_API_TOKEN']
        )
        print("   Adapter initialized successfully")
    except Exception as e:
        print(f"   Error initializing adapter: {e}")
        # Clean up
        os.unlink(main_image_path)
        return
    
    # Create a test post
    print("\n4. Creating test post in Sanity...")
    try:
        result = adapter.post_blog(
            title="Embedded Images Test Post",
            summary="A test post to verify embedded image handling",
            content=test_markdown,
            categories=["Testing", "Images"],
            local_image_path=main_image_path,
            slug="embedded-images-test",
            alt_text="Test post main image",
            faqs=[
                {
                    "question": "Does this support embedded images?",
                    "answer": "Yes, this test verifies embedded image handling."
                }
            ]
        )
        
        print(f"   Post creation result: {result.get('status')}")
        if result.get('status') == 'success':
            print(f"   Post ID: {result.get('post_id')}")
            print(f"   Post URL: {result.get('post_url')}")
            print("+ Workflow completed successfully!")
        else:
            print(f"   Error: {result.get('error')}")
            print("- Workflow failed")
            
    except Exception as e:
        print(f"   Error creating post: {e}")
        print("- Workflow failed")
    finally:
        # Clean up
        if os.path.exists(main_image_path):
            os.unlink(main_image_path)

def main():
    """Run the complete workflow test."""
    print("Complete Workflow Test")
    print("=" * 30)
    
    # Load environment variables if .env file exists
    env_file = project_root / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
    
    # Run test
    test_full_workflow()
    
    print("\n" + "=" * 30)
    print("Test completed.")

if __name__ == "__main__":
    main()