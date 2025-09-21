#!/usr/bin/env python3
"""
Test script to verify Sanity image handling with markdown content.
This script tests:
1. Markdown parsing with image syntax
2. Image uploading to Sanity
3. Content posting to Sanity with embedded images
"""

import os
import sys
import json
import tempfile
from pathlib import Path

# Add the project root to the path so we can import our modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from lib.sanity_adapter import SanityAdapter
from lib.markdown_parser import markdown_to_sanity_blocks

def create_test_image():
    """Create a simple test image for testing purposes."""
    try:
        from PIL import Image, ImageDraw
        # Create a simple test image
        img = Image.new('RGB', (200, 200), color=(73, 109, 137))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "Test Image", fill=(255, 255, 0))
        
        # Save to temporary file
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        img.save(temp_file.name)
        return temp_file.name
    except Exception as e:
        print(f"Error creating test image: {e}")
        return None

def test_markdown_image_parsing():
    """Test if markdown parser correctly handles image syntax."""
    print("=== Testing Markdown Image Parsing ===")
    
    # Test markdown with image
    test_markdown = """# Test Post with Images

This is a test post with an image.

![Alt text for image](https://example.com/test-image.jpg)

More content after the image.

![Another image](/local/path/image.png "Image with title")

End of post."""
    
    print("Input markdown:")
    print(test_markdown)
    print("\nParsing to Sanity blocks...")
    
    try:
        blocks = markdown_to_sanity_blocks(test_markdown, debug=True)
        print(f"\nGenerated {len(blocks)} blocks:")
        print(json.dumps(blocks, indent=2))
        
        # Check if images were parsed
        image_found = False
        for block in blocks:
            if block.get('_type') == 'block':
                for child in block.get('children', []):
                    if 'image' in child.get('text', '').lower():
                        image_found = True
                        break
        
        if image_found:
            print("\n✓ Image text found in parsed content")
        else:
            print("\n⚠ No explicit image handling detected (images may be treated as regular text)")
            
        return blocks
    except Exception as e:
        print(f"Error parsing markdown: {e}")
        return []

def test_sanity_image_upload():
    """Test uploading an image to Sanity."""
    print("\n=== Testing Sanity Image Upload ===")
    
    # Check if required environment variables are set
    required_vars = ['SANITY_PROJECT_ID', 'SANITY_API_TOKEN']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"⚠ Missing environment variables: {missing_vars}")
        print("Skipping Sanity upload test.")
        return None
    
    # Create a test image
    image_path = create_test_image()
    if not image_path:
        print("⚠ Failed to create test image")
        return None
    
    try:
        print(f"Created test image at: {image_path}")
        
        # Initialize Sanity adapter
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET', 'production'),
            token=os.environ['SANITY_API_TOKEN']
        )
        
        # Upload image
        print("Uploading image to Sanity...")
        result = adapter.upload_image(image_path)
        
        print(f"Upload result: {json.dumps(result, indent=2)}")
        
        # Clean up
        os.unlink(image_path)
        
        if result.get('success'):
            print("✓ Image uploaded successfully")
            return result
        else:
            print("✗ Image upload failed")
            return None
            
    except Exception as e:
        print(f"Error uploading image: {e}")
        # Clean up
        if os.path.exists(image_path):
            os.unlink(image_path)
        return None

def test_markdown_with_images_to_sanity():
    """Test the full flow of markdown with images to Sanity."""
    print("\n=== Testing Full Flow: Markdown with Images to Sanity ===")
    
    # Check environment variables
    required_vars = ['SANITY_PROJECT_ID', 'SANITY_API_TOKEN', 'SANITY_DEFAULT_AUTHOR_ID']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"⚠ Missing environment variables: {missing_vars}")
        print("Skipping full flow test.")
        return False
    
    # Create test image
    image_path = create_test_image()
    if not image_path:
        print("⚠ Failed to create test image")
        return False
    
    try:
        # Test markdown content with image
        test_content = """# Test Blog Post with Embedded Images

This is a test blog post to verify that images are correctly handled.

![Test Image Alt Text](https://example.com/test-image.jpg)

Here's some content between images.

![Local Test Image](/path/to/local/image.png "Image with title")

More content at the end of the post."""

        print("Test content:")
        print(test_content)
        
        # Parse markdown
        print("\nParsing markdown to Sanity blocks...")
        content_blocks = markdown_to_sanity_blocks(test_content)
        print(f"Parsed into {len(content_blocks)} blocks")
        
        # Initialize Sanity adapter
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET', 'production'),
            token=os.environ['SANITY_API_TOKEN']
        )
        
        # Test blog post creation (this would normally include the image)
        print("\nCreating test blog post...")
        result = adapter.post_blog(
            title="Test Post with Images",
            summary="A test post to verify image handling",
            content=test_content,  # Using raw markdown content
            categories=["Testing", "Images"],
            local_image_path=image_path,  # Featured image
            slug="test-post-with-images",
            alt_text="Test image alt text",
            faqs=[
                {"question": "Does this support images?", "answer": "Testing image support in Sanity."}
            ]
        )
        
        print(f"Post creation result: {json.dumps(result, indent=2)}")
        
        # Clean up
        os.unlink(image_path)
        
        if result.get('status') == 'success':
            print("✓ Blog post created successfully with image")
            return True
        else:
            print("✗ Blog post creation failed")
            return False
            
    except Exception as e:
        print(f"Error in full flow test: {e}")
        # Clean up
        if os.path.exists(image_path):
            os.unlink(image_path)
        return False

def main():
    """Run all tests."""
    print("Sanity Image Handling Test Suite")
    print("=" * 40)
    
    # Load environment variables if .env file exists
    env_file = project_root / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
    
    # Run tests
    test_markdown_image_parsing()
    test_sanity_image_upload()
    test_markdown_with_images_to_sanity()
    
    print("\n" + "=" * 40)
    print("Test suite completed.")

if __name__ == "__main__":
    main()