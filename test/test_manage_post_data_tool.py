import json
import asyncio
from tools import manage_post_data_tool

ctx = None  # Dummy context, not used by the tool

async def main():
    # Test add_post
    add_post_input = json.dumps({
        "action": "add_post",
        "title": "Test Blog Post",
        "summary": "This is a test summary.",
        "content": "# Test Blog Post\n\nThis is a test post.",
        "funnel_level": "TOFU",
        "categories": ["AI", "social media"],
        "headings": ["Introduction", "Benefits"],
        "slug": "test-blog-post",
        "status": "posted"
    })
    add_post_result = await manage_post_data_tool.on_invoke_tool(ctx, add_post_input)
    print("Add Post Result:", add_post_result)

    # Test get_related_posts
    related_posts_input = json.dumps({
        "action": "get_related_posts",
        "funnel_level": "TOFU",
        "categories": ["AI", "social media"],
        "title": "",
        "summary": "",
        "content": "",
        "headings": [],
        "slug": "",
        "status": "posted"
    })
    related_posts_result = await manage_post_data_tool.on_invoke_tool(ctx, related_posts_input)
    print("Related Posts Result:", related_posts_result)

    # Test update_status
    update_status_input = json.dumps({
        "action": "update_status",
        "slug": "test-blog-post",
        "status": "archived"
    })
    update_status_result = await manage_post_data_tool.on_invoke_tool(ctx, update_status_input)
    print("Update Status Result:", update_status_result)

if __name__ == "__main__":
    asyncio.run(main()) 