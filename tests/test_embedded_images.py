#!/usr/bin/env python3
"""
Test script to verify embedded image handling in Sanity posts.
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

def test_embedded_image_processing():
    """Test that embedded images are correctly processed."""
    print("=== Testing Embedded Image Processing ===")
    
    # Create test markdown with embedded images
    test_markdown = """# Test Post with Embedded Images

This is a test post to verify image processing.

![First test image](https://httpbin.org/image/jpeg)

Here's some content between images.

![Second test image](https://httpbin.org/image/png "Image with title")

More content at the end."""

    print("Input markdown:")
    print(test_markdown)
    
    # Parse to Sanity blocks
    blocks = markdown_to_sanity_blocks(test_markdown, debug=True)
    
    print(f"\nGenerated {len(blocks)} blocks:")
    
    # Check for image blocks
    image_blocks = [block for block in blocks if block.get('_type') == 'image']
    print(f"\nFound {len(image_blocks)} image blocks:")
    
    for i, block in enumerate(image_blocks, 1):
        print(f"\nImage Block {i}:")
        print(json.dumps(block, indent=2))
        
        # Verify structure
        assert '_type' in block and block['_type'] == 'image'
        assert 'asset' in block
        assert 'alt' in block
        print("+ Block structure is correct")
        
        # Check if it has a URL
        if 'url' in block['asset']:
            print(f"+ Has URL: {block['asset']['url']}")
        else:
            print("+ No URL in asset (may be reference)")

def test_sanity_image_upload():
    """Test uploading images to Sanity."""
    print("\n=== Testing Sanity Image Upload ===")
    
    # Check if we have Sanity credentials
    required_vars = ['SANITY_PROJECT_ID', 'SANITY_API_TOKEN']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"Note: Missing environment variables {missing_vars}")
        print("Skipping Sanity upload test.")
        return
    
    # Create a simple test image
    try:
        from PIL import Image, ImageDraw
        # Create a simple test image
        img = Image.new('RGB', (100, 100), color=(73, 109, 137))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "Test", fill=(255, 255, 0))
        
        # Save to temporary file
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        img.save(temp_file.name)
        temp_path = temp_file.name
        temp_file.close()
        
        print(f"Created test image: {temp_path}")
        
        # Initialize Sanity adapter
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET', 'production'),
            token=os.environ['SANITY_API_TOKEN']
        )
        
        # Upload image
        print("Uploading image to Sanity...")
        result = adapter.upload_image(temp_path)
        
        print(f"Upload result: {json.dumps(result, indent=2)}")
        
        # Clean up
        os.unlink(temp_path)
        
        if result.get('success'):
            print("+ Image uploaded successfully")
            return result
        else:
            print("- Image upload failed")
            return None
            
    except Exception as e:
        print(f"Error creating/uploading test image: {e}")
        # Clean up if needed
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)
        return None

def main():
    """Run all tests."""
    print("Embedded Image Handling Test Suite")
    print("=" * 40)
    
    # Load environment variables if .env file exists
    env_file = project_root / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
    
    # Run tests
    test_embedded_image_processing()
    test_sanity_image_upload()
    
    print("\n" + "=" * 40)
    print("Test suite completed.")

if __name__ == "__main__":
    main()