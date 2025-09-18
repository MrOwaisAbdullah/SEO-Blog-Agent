from tools import manage_post_data_tool

print(manage_post_data_tool(
    action="add_post",
    title="Test Post",
    summary="Test summary",
    content="# Test Post\n## Summary\nTest content",
    funnel_level="TOFU",
    categories=["AI", "social media"],
    headings=["Introduction", "Conclusion"],
    slug="test-post",
    status="pending"
))