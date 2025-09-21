#!/usr/bin/env python3
"""
Test script to verify markdown image parsing functionality.
"""

import sys
import json
from pathlib import Path

# Add the project root to the path so we can import our modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from lib.markdown_parser import markdown_to_sanity_blocks

def test_basic_image_parsing():
    """Test basic image parsing from markdown."""
    print("=== Test 1: Basic Image Parsing ===")
    
    markdown = """# Test Post

This is a paragraph with an image:

![Alt text for image](https://example.com/image.jpg)

Another paragraph."""

    print("Input markdown:")
    print(markdown)
    
    blocks = markdown_to_sanity_blocks(markdown, debug=False)
    print(f"\nGenerated {len(blocks)} blocks:")
    
    # Check for image block
    image_block_found = False
    for block in blocks:
        if block.get('_type') == 'image':
            print(f"+ Found image block: {json.dumps(block, indent=2)}")
            image_block_found = True
        elif block.get('_type') == 'block':
            # Check if this is a paragraph that contains only an image
            children = block.get('children', [])
            if len(children) == 1:
                child = children[0]
                if '![Alt text for image]' in child.get('text', ''):
                    print(f"- Image treated as text in block: {json.dumps(block, indent=2)}")
    
    if not image_block_found:
        print("- No image block found - images may be treated as text")
    
    print()

def test_image_with_title():
    """Test image with title attribute."""
    print("=== Test 2: Image with Title ===")
    
    markdown = """![Image with title](/path/to/image.png "This is the title")"""
    
    print("Input markdown:")
    print(markdown)
    
    blocks = markdown_to_sanity_blocks(markdown, debug=False)
    print(f"\nGenerated {len(blocks)} blocks:")
    
    for block in blocks:
        if block.get('_type') == 'image':
            print(f"+ Found image block: {json.dumps(block, indent=2)}")
            if 'title' in block:
                print(f"+ Title preserved: {block['title']}")
            if 'alt' in block:
                print(f"+ Alt text preserved: {block['alt']}")
    
    print()

def test_multiple_images():
    """Test multiple images in content."""
    print("=== Test 3: Multiple Images ===")
    
    markdown = """# Post with Multiple Images

First image:
![First image](https://example.com/first.jpg)

Some text between images.

Second image:
![Second image](https://example.com/second.png "Second image title")

End of post."""

    print("Input markdown:")
    print(markdown)
    
    blocks = markdown_to_sanity_blocks(markdown, debug=False)
    print(f"\nGenerated {len(blocks)} blocks:")
    
    image_count = 0
    for block in blocks:
        if block.get('_type') == 'image':
            image_count += 1
            print(f"Image {image_count}: {json.dumps(block, indent=2)}")
    
    print(f"+ Found {image_count} image blocks")
    print()

def test_mixed_content():
    """Test mixed content with images, headings, and text."""
    print("=== Test 4: Mixed Content ===")
    
    markdown = """# Main Heading

This is a paragraph with **bold text** and *italic text*.

![Featured Image](https://example.com/featured.jpg "Featured image title")

## Subheading

Another paragraph with a [link](https://example.com) and more text.

![Inline Image](/local/image.png)

- List item 1
- List item 2

> This is a blockquote."""

    print("Input markdown:")
    print(markdown)
    
    blocks = markdown_to_sanity_blocks(markdown, debug=False)
    print(f"\nGenerated {len(blocks)} blocks:")
    
    block_types = {}
    for block in blocks:
        block_type = block.get('_type', 'unknown')
        block_types[block_type] = block_types.get(block_type, 0) + 1
        if block_type == 'image':
            print(f"Image block: {json.dumps(block, indent=2)}")
    
    print(f"Block types found: {block_types}")
    print()

def main():
    """Run all tests."""
    print("Markdown Image Parsing Test Suite")
    print("=" * 40)
    
    test_basic_image_parsing()
    test_image_with_title()
    test_multiple_images()
    test_mixed_content()
    
    print("=" * 40)
    print("Test suite completed.")

if __name__ == "__main__":
    main()