"""
CLI entrypoint for running a single ContentSpark pipeline stage.

Invoked by .github/workflows/pipeline.yml (on a schedule, or via
workflow_dispatch triggered from the Discord bot's slash commands) instead of
the old FastAPI endpoints in main.py. Reuses the exact same agent-invocation
logic as main.py's /research, /generate_brief, /generate_content,
/post_content routes -- this script just runs it as a one-shot process instead
of behind an HTTP server, so a scheduler doesn't need to hold an HTTP
connection open for however long a stage takes.

Unlike the old endpoints (which always return 200 with a status field), this
script exits non-zero on genuine failures so a failed stage shows red in the
GitHub Actions UI. "Nothing to do" (empty queue) is treated as success, same
as the old endpoints.
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import requests

from agents import set_tracing_disabled
from agents.run import set_default_agent_runner

from blog_agent.blog_agents import brief_agent, content_generator_agent
from blog_agent.custom_runner import FallbackAgentRunner
from blog_agent.posting_agent import run_posting_workflow
from blog_agent.research_agent import combined_research_workflow
from tools.sheet_tool import manage_sheet_data

MAX_TURNS = 30

# "Queue is empty today" is a normal, successful outcome -- don't fail the
# Action run for it, same as the old endpoints treated it as a 200.
BENIGN_EMPTY_MARKERS = (
    "no ungenerated rows found",
    "no ungenerated briefs found",
    "no available keywords",
    "no_posts_found",
    "no approved, unpublished posts found",
)

custom_runner = FallbackAgentRunner()
# All inference goes through Gemini/OpenRouter/Cohere via custom clients,
# never OpenAI's API, so tracing export is disabled (same as main.py).
set_tracing_disabled(True)
set_default_agent_runner(custom_runner)


def _is_benign_empty(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BENIGN_EMPTY_MARKERS)


def _notify_discord(title: str, summary: str) -> None:
    """Posts a draft-ready-for-review message. The Discord bot listens for
    reactions on this message and matches them back to the sheet row by
    Title, so the title must be included verbatim."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set; skipping approval notification.")
        return

    content = (
        "**New Draft Ready for Review**\n\n"
        f"**Title:** {title}\n"
        f"**Summary:** {summary}\n\n"
        "React with ✅ to approve or ❌ to reject."
    )
    try:
        response = requests.post(webhook_url, json={"content": content}, timeout=10)
        response.raise_for_status()
    except Exception as e:
        # A failed notification shouldn't fail the whole stage -- the post
        # was already generated and saved; it just needs manual approval via
        # the sheet if Discord notification didn't go through.
        print(f"Failed to send Discord notification: {e}")


async def run_research() -> None:
    result = await combined_research_workflow(
        LLM_MODELS=custom_runner.LLM_MODELS,
        is_model_available=custom_runner.is_model_available,
        get_model_by_name=custom_runner.get_model_by_name,
        increment_usage=custom_runner.increment_usage,
        MAX_TURNS=15,
    )
    print(f"[research] result: {result}")
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"Research stage failed: {result['error']}")


async def run_brief() -> None:
    result = await custom_runner.run_with_fallback(
        brief_agent,
        "Generate content brief based on the first available research findings that is not generated yet from the research_data worksheet.",
        max_turns=MAX_TURNS,
    )
    output = str(getattr(result, "final_output", result))
    print(f"[brief] result: {output}")
    if "error" in output.lower() and not _is_benign_empty(output):
        raise RuntimeError(f"Brief stage failed: {output}")


async def run_content() -> None:
    result = await custom_runner.run_with_fallback(
        content_generator_agent,
        "Generate content based on the first available brief that is not generated yet from the content_briefs worksheet and add it to the generated_posts worksheet.",
        max_turns=MAX_TURNS,
    )
    output = str(getattr(result, "final_output", result))
    print(f"[content] result: {output}")
    if "error" in output.lower() and not _is_benign_empty(output):
        raise RuntimeError(f"Content stage failed: {output}")

    if _is_benign_empty(output):
        return

    # Read back the row that was just written and notify Discord for approval.
    records = manage_sheet_data(worksheet_name="generated_posts", action="get_all_records")
    if records.get("status") == "success" and records.get("data"):
        last_row = records["data"][-1]
        _notify_discord(
            title=last_row.get("Title", "Untitled"),
            summary=last_row.get("Summary", ""),
        )
    else:
        print("Could not read back generated_posts to send Discord notification.")


async def run_post() -> None:
    result = await run_posting_workflow()
    print(f"[post] result: {result}")
    if isinstance(result, dict) and result.get("status") == "error":
        raise RuntimeError(f"Posting stage failed: {result.get('error')}")


STAGE_HANDLERS = {
    "research": run_research,
    "brief": run_brief,
    "content": run_content,
    "post": run_post,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single ContentSpark pipeline stage.")
    parser.add_argument("--stage", required=True, choices=sorted(STAGE_HANDLERS))
    args = parser.parse_args()

    try:
        asyncio.run(STAGE_HANDLERS[args.stage]())
    except Exception as e:
        print(f"Stage '{args.stage}' failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
