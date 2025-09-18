import os
from tools.tools import post_to_sanity_tool
import asyncio

async def test_post():
    image_path = "D:/GIAIC/Real World Projects/SEO Blog Agent/image.png"
    
    # --- Add Debugging Checks ---
    print(f"Checking image file: {image_path}")
    if not os.path.exists(image_path):
        print(f"ERROR: File does not exist at {image_path}")
        return
    else:
        print("File exists.")
        
    file_size = os.path.getsize(image_path)
    print(f"File size: {file_size} bytes")
    if file_size == 0:
        print("ERROR: File is empty.")
        return
        
    # Try to read the first few bytes to see if it seems like an image
    try:
        with open(image_path, 'rb') as f:
            header = f.read(10) # Read first 10 bytes
            print(f"First 10 bytes (hex): {header.hex()}")
            # PNG files usually start with 89 50 4E 47 0D 0A 1A 0A
            # JPEG files usually start with FF D8 FF
            # You can add checks here if needed, but viewing it is often easier.
    except Exception as read_error:
        print(f"ERROR: Could not read file: {read_error}")
        return
    # --- End Add Debugging Checks ---
    result = await post_to_sanity_tool(
        title="Test Blog",
        summary="Test summary",
        content="""
        # Main Heading
This is a **bold** paragraph with *italic* text and a ~~strikethrough~~ and <u>underline</u>.

## Subheading

Here's a list:
- Item 1 with `inline code`
- Item 2 with more text
> This is a blockquote with **bold** text.
Final paragraph with some text.

```python
a + b = c
a = 10
```

        ## Test content
        This is a test blog post to verify the **functionality** of the *Sanity* posting tool. It includes various sections and formatting to ensure everything works as expected.
        
        ### Heading
        This is a test heading.
        
        ### Subheading
        - Item 1
        - Item 2
        - Item 3

        ### Ordered List

        1. First item
        2. Second item
        3. Third item

        
        ### Code Block
        ### Conclusion
        This concludes the test blog post. It should be comprehensive enough to check all functionalities of the Sanity posting tool.
        """,
        categories=["AI", "Social Media"],
        image_path="D:/GIAIC/Real World Projects/SEO Blog Agent/image.png",
        slug="test-blog-42",
        alt_text="Test image alt",
        faqs=[
            {"question": "What is AI?", "answer": "AI stands for Artificial Intelligence."},
            {"question": "How does social media automation work?", "answer": "Social media automation uses tools to schedule and manage posts."}
        ]
    )
    print(result)

asyncio.run(test_post())