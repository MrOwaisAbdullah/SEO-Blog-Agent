import asyncio
from blog_agents import posting_agent
from llm_clients import run_flow_with_agent_fallback, LLM_MODELS, is_model_available, get_model_by_name, increment_usage
# from tools import manage_post_data_tool
# from rich import print

# Example input for posting_agent: a revised blog post and a content brief (as expected by your agent)
# You may need to adjust the structure to match your PostingOutput/input_type if you use one.

sample_input = """
    "revised_post": "```markdown\n# How AI Social Media Tools Save Agencies Time 🚀\n\n**Summary:** In today's fast-paced digital landscape, social media agencies face the constant challenge of delivering top-notch content while managing multiple client accounts. AI-powered tools offer a streamlined solution by automating tasks, ensuring consistent brand voice, and freeing up valuable time. Discover how agencies can leverage AI to boost efficiency and achieve better client results.\n\n## Introduction: AI - The Social Media Agency's New Best Friend\n\nThe social media world never stops evolving, and agencies need to stay ahead. Managing multiple clients, creating killer content, and tracking results can be overwhelming. That's where **AI social media tools for agencies** step in. They're not just a nice-to-have; they're essential for agencies aiming to dominate the market. ✅\n\n## Why Agencies Need AI: Time is Money, Consistency is Key\n\nAgencies are always under pressure to deliver great results fast. Time is a major constraint, and inefficient processes hurt productivity. **AI social media tools** tackle these issues by automating tasks like scheduling, optimizing posts, and generating reports.\n\nMaintaining **brand voice consistency** across all clients is another hurdle. Each brand has a unique identity, and content must reflect that. AI tools analyze existing content, providing guidelines to maintain a consistent tone and style, saving time and ensuring brand integrity.\n\n## AI-Powered Automation: Supercharging Content Scheduling and Posting\n\nOne of AI's biggest strengths is automating repetitive social media tasks. **Social media automation** tools schedule posts across platforms, ensuring a steady stream of content. AI algorithms also optimize posting times based on when your audience is most active, maximizing reach. This frees agencies to focus on strategy, creative campaigns, and building influencer relationships.\n\n## Ensuring Brand Voice Consistency: AI's Secret Weapon for Brand Identity\n\nA consistent brand voice builds recognition and trust. AI tools analyze content and provide real-time feedback to ensure new content aligns with the brand's style. This helps agencies deliver a unified brand experience, strengthening brand identity and boosting customer loyalty.\n\n## SocialAuto Solutions: AI-Powered Social Media Management\n\nAt SocialAuto Solutions, we get the unique challenges social media agencies face. That's why we've built a suite of AI tools to streamline workflows and boost results.\n\n*   **Smart Scheduling:** AI-powered scheduling that learns when your audience is most engaged.\n*   **Brand Voice Guardian:** Ensures every post aligns with your client's brand identity.\n*   **Performance Dashboard:** Track key metrics and gain actionable insights.\n*   **Competitor Intel:** Stay ahead of the competition with AI-driven competitor analysis.\n\n✅ SocialAuto Solutions helps agencies save time, increase engagement, and accelerate brand growth.\n\n**Example:** One of our agency clients, \"Digital Edge,\" used SocialAuto Solutions to automate their content scheduling. They saw a 30% increase in engagement and saved 15 hours per week.\n\n## Conclusion: The AI-Driven Future of Social Media Management\n\n**AI social media tools for agencies** are revolutionizing agency operations. By automating tasks, ensuring brand voice consistency, and offering valuable insights, these tools empower agencies to deliver exceptional client results. As AI evolves, expect even more innovative solutions to transform the social media landscape.\n\n**Categories:** AI, social media, automation, brand consistency, agencies\n```",
    "content_brief": {
        "title": "AI Social Media Tools Save Agencies Time",
        "summary": "AI Social Media Tools Save Agencies Time in difital age",
        "categories": ["AI", "social media"],
        "slug": "sample-blog-post",
        "funnel_level": "TOFU",
        "keywords": ["AI", "social media tools"]
                """

async def test_posting_agent():
    print("=== Testing Posting Agent with Fallback ===")
    result = await run_flow_with_agent_fallback(
        posting_agent,
        sample_input,
        LLM_MODELS,
        is_model_available,
        get_model_by_name,
        increment_usage,
        max_retries=3,
        max_turns=10
    )
    print("Posting Agent Result:", result)

if __name__ == "__main__":
    asyncio.run(test_posting_agent()) 

# print(manage_post_data_tool.on_invoke_tool)