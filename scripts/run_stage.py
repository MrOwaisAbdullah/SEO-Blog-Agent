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
import json
import os
import re
import sys
from typing import Optional

# When Python runs a script by path (`python scripts/run_stage.py`), it puts
# the script's own directory on sys.path[0], not the repo root -- so sibling
# top-level packages (blog_agent, tools, lib) aren't importable otherwise.
# This must happen before any project imports below.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import requests

from agents import set_tracing_disabled
from agents.run import set_default_agent_runner

from blog_agent.blog_agents import brief_agent, content_generator_agent
from blog_agent.custom_runner import FallbackAgentRunner
from blog_agent.posting_agent import run_posting_workflow
from blog_agent.research_agent import combined_research_workflow, run_topic_discovery_workflow
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


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _parse_agent_json(output: str):
    """Parses a brief/content agent's JSON response, stripping a ```json
    fence if present. Returns None if the output isn't a valid JSON object."""
    fence_match = _JSON_FENCE_RE.search(output)
    json_text = fence_match.group(1) if fence_match else output.strip()
    try:
        parsed = json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _get_field(d: dict, name: str, default=""):
    """The prompt examples show exact key casing/spacing (e.g.
    "Keyword/Topic", "Brief Content"), but fallback models don't reliably
    match it -- confirmed live: Cohere returned "keyword/topic" and
    "brief_content" for the same fields in one run, then the properly-cased
    keys in another. Match keys after stripping case and non-alphanumeric
    characters instead of requiring an exact string match.

    Also confirmed live: a different Cohere run nested the actual fields
    under a "data" wrapper instead of returning them flat
    ({"status": ..., "message": ..., "data": {"Keyword/Topic": ...}}), even
    though the prompt's example shows a flat structure. If the field isn't
    found at the top level, check one level into any dict-valued top-level
    field too -- covers "data"/"result"/"brief"-style wrappers without
    needing to special-case a specific wrapper key name."""
    target = _normalize_key(name)
    for key, value in d.items():
        if _normalize_key(key) == target:
            return value
    for value in d.values():
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if _normalize_key(nested_key) == target:
                    return nested_value
    return default


def _agent_output_indicates_error(output: str) -> bool:
    """Every brief/content agent is instructed to return JSON with an
    explicit "status" field, always including an "errors": [] key even on
    success. Naively checking `"error" in output.lower()` false-positives on
    that field's own name -- parse the JSON and trust its actual status
    instead. If the output isn't valid JSON at all, that's itself treated as
    a failure worth surfacing rather than silently guessed at with more
    substring heuristics.
    """
    parsed = _parse_agent_json(output)
    if parsed is None:
        return True
    return _get_field(parsed, "status") == "error"


# Discord's hard cap is 2000 chars per message; leave headroom for the
# "(n/total)" prefix we add to each chunk.
DISCORD_MESSAGE_LIMIT = 1900


def _chunk_for_discord(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list:
    """Splits text into <=limit-char chunks on paragraph/line boundaries so
    Markdown (headings, lists, links) doesn't get cut mid-token."""
    if not text:
        return []
    chunks = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _post_discord_message(webhook_url: str, content: str) -> None:
    response = requests.post(webhook_url, json={"content": content}, timeout=10)
    response.raise_for_status()


def _notify_discord(title: str, summary: str, content: str = "", faqs: str = "") -> None:
    """Posts a draft-ready-for-review message, then the full post body (and
    FAQs) as follow-up messages so a reviewer can read the whole thing,
    correctly rendered, without opening the sheet. The Discord bot listens
    for reactions on the FIRST message only (the one with the embedded
    Title) and matches them back to the sheet row by Title, so that
    message's format must stay exactly as-is."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set; skipping approval notification.")
        return

    header = (
        "**New Draft Ready for Review**\n\n"
        f"**Title:** {title}\n"
        f"**Summary:** {summary}\n\n"
        "React with ✅ to approve or ❌ to reject."
    )
    try:
        _post_discord_message(webhook_url, header)
    except Exception as e:
        # A failed notification shouldn't fail the whole stage -- the post
        # was already generated and saved; it just needs manual approval via
        # the sheet if Discord notification didn't go through.
        print(f"Failed to send Discord notification: {e}")
        return

    body_chunks = _chunk_for_discord(content)
    for i, chunk in enumerate(body_chunks, start=1):
        prefix = f"**Post ({i}/{len(body_chunks)})**\n\n" if len(body_chunks) > 1 else "**Post**\n\n"
        try:
            _post_discord_message(webhook_url, prefix + chunk)
        except Exception as e:
            print(f"Failed to send Discord post-content chunk {i}/{len(body_chunks)}: {e}")

    if not faqs:
        return
    try:
        parsed_faqs = json.loads(faqs)
        faq_text = "\n\n".join(
            f"**Q: {item.get('question', '')}**\nA: {item.get('answer', '')}" for item in parsed_faqs
        )
    except (json.JSONDecodeError, TypeError, AttributeError):
        faq_text = faqs
    faq_chunks = _chunk_for_discord(faq_text)
    for i, chunk in enumerate(faq_chunks, start=1):
        prefix = f"**FAQs ({i}/{len(faq_chunks)})**\n\n" if len(faq_chunks) > 1 else "**FAQs**\n\n"
        try:
            _post_discord_message(webhook_url, prefix + chunk)
        except Exception as e:
            print(f"Failed to send Discord FAQ chunk {i}/{len(faq_chunks)}: {e}")


def _notify_discord_status(stage: str, success: bool, detail: str = "") -> None:
    """Posts a one-line status update for every stage run so failures show
    up in Discord instead of only in the Actions log."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return
    icon = "✅" if success else "❌"
    content = f"{icon} Stage `{stage}` {'completed' if success else 'failed'}."
    if detail:
        content += f"\n```{detail[:1800]}```"
    try:
        _post_discord_message(webhook_url, content)
    except Exception as e:
        print(f"Failed to send Discord status notification: {e}")


def _notify_discord_topic_candidates(candidates: list) -> None:
    """Posts each trending-topic candidate as its OWN message so it can carry
    its own ✅/❌ reaction, same granular approval pattern as draft posts. The
    bot matches a reaction back to a candidate by the "**Candidate Topic:**"
    line (parallel to how draft approvals match on "**Title:**"), so that
    line's format must stay exactly as-is."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set; skipping topic candidate notification.")
        return
    for candidate in candidates:
        topic = str(candidate.get("topic", "")).strip()
        rationale = str(candidate.get("rationale", "")).strip()
        if not topic:
            continue
        content = (
            "**New Topic Candidate**\n\n"
            f"**Candidate Topic:** {topic}\n"
            f"**Why now:** {rationale}\n\n"
            "React with ✅ to add this to the research queue, or ❌ to skip it."
        )
        try:
            _post_discord_message(webhook_url, content)
        except Exception as e:
            print(f"Failed to send Discord topic candidate notification for '{topic}': {e}")


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

    if isinstance(result, dict) and result.get("status") == "no_available_keywords":
        # Nothing queued in ContentSpark_Keywords -- rather than just doing
        # nothing until someone manually adds a topic, look for what's
        # actually being discussed right now and propose it for approval.
        print("[research] No available keywords; running topic discovery instead.")
        await run_discover_topics()


async def run_discover_topics() -> None:
    result = await run_topic_discovery_workflow()
    print(f"[discover_topics] result: {result}")
    if result.get("status") == "error":
        raise RuntimeError(f"Topic discovery failed: {result.get('error')}")
    if result.get("status") == "no_candidates_found" or not result.get("candidates"):
        print("[discover_topics] No current topic candidates found.")
        return
    _notify_discord_topic_candidates(result["candidates"])


def _ensure_brief_persisted(brief: dict) -> None:
    """The Brief Agent is instructed to save its own output via
    manage_sheet_data_tool, but a fallback model can generate a fully
    correct JSON answer and then simply stop instead of actually calling the
    tool -- confirmed live (Cohere, after a Gemini 429): the stage reported
    "success" with a complete brief in its output, but content_briefs never
    got the row. Verify the row actually exists here and write it
    deterministically if not, so a stage can't report success without
    actually persisting its output -- the same class of fix already applied
    to the posting chain's marker round-tripping."""
    keyword = str(_get_field(brief, "Keyword/Topic")).strip()
    if not keyword:
        raise RuntimeError(f"Brief output missing Keyword/Topic; cannot persist or verify. Keys seen: {list(brief.keys())}")

    existing = manage_sheet_data(worksheet_name="content_briefs", action="get_all_records")
    already_saved = existing.get("status") == "success" and any(
        str(_get_field(row, "Keyword/Topic")).strip() == keyword for row in existing.get("data", [])
    )

    if already_saved:
        print(f"[brief] content_briefs already has a row for '{keyword}'; agent saved it correctly.")
    else:
        faqs = _get_field(brief, "FAQs", [])
        faqs_str = faqs if isinstance(faqs, str) else json.dumps(faqs)
        append_result = manage_sheet_data(
            worksheet_name="content_briefs",
            action="append_row",
            row_values=[
                keyword,
                str(_get_field(brief, "Brief Content")),
                faqs_str,
                str(_get_field(brief, "External Source Links")),
                str(_get_field(brief, "Content Summary")),
                "No",
            ],
        )
        if append_result.get("status") != "success":
            raise RuntimeError(f"Failed to persist brief to content_briefs: {append_result}")
        print(f"[brief] Appended row to content_briefs for '{keyword}' (agent reported success but had not saved it).")

    # Mark the source research_data row as consumed so the next brief run
    # doesn't pick up the same row again.
    lookup = manage_sheet_data(
        worksheet_name="research_data",
        action="find_row_by_key",
        key_column="Keyword/Topic",
        key_value=keyword,
    )
    if not (lookup.get("status") == "success" and lookup.get("found")):
        print(f"[brief] Warning: could not find research_data row for '{keyword}' to mark as Generated.")
        return
    if str((lookup.get("data") or {}).get("Generated", "")).strip().lower() == "yes":
        print(f"[brief] research_data row for '{keyword}' already marked Generated=Yes.")
        return

    headers_result = manage_sheet_data(worksheet_name="research_data", action="get_range", cell_range="1:1")
    header_row = (headers_result.get("data") or [[]])[0] if headers_result.get("status") == "success" else []
    if "Generated" not in header_row:
        print("[brief] Warning: 'Generated' column not found in research_data headers; cannot mark row consumed.")
        return
    update_result = manage_sheet_data(
        worksheet_name="research_data",
        action="update_cell",
        row_index=lookup["row_index"],
        col_index=header_row.index("Generated") + 1,
        data="Yes",
    )
    if update_result.get("status") != "success":
        print(f"[brief] Warning: failed to mark research_data row {lookup['row_index']} as Generated=Yes: {update_result}")
    else:
        print(f"[brief] Marked research_data row {lookup['row_index']} as Generated=Yes for '{keyword}'.")


def _tool_call_succeeded(run_result, expect_in_message: str) -> bool:
    """Scans a RunResult's actual tool_call_output items for a
    manage_sheet_data-shaped success whose message contains
    expect_in_message. Confirmed live: a fallback model can execute every
    required tool call correctly (append_row to content_briefs, update_cell
    on research_data) and then summarize the result as plain narrative text
    instead of the documented JSON envelope -- treating "not JSON" as an
    automatic failure in that case would raise on a run that had already
    fully succeeded. Check what actually happened via the tool outputs
    instead of requiring the final answer to be in any particular format."""
    new_items = getattr(run_result, "new_items", None) or []
    for item in new_items:
        if getattr(item, "type", None) != "tool_call_output_item":
            continue
        output = getattr(item, "output", None)
        if isinstance(output, dict) and output.get("status") == "success" and expect_in_message in str(output.get("message", "")):
            return True
    return False


async def run_brief() -> None:
    result = await custom_runner.run_with_fallback(
        brief_agent,
        "Generate content brief based on the first available research findings that is not generated yet from the research_data worksheet.",
        max_turns=MAX_TURNS,
    )
    output = str(getattr(result, "final_output", result))
    print(f"[brief] result: {output}")

    if _is_benign_empty(output):
        return

    parsed = _parse_agent_json(output)
    if parsed is not None:
        if _get_field(parsed, "status") == "error":
            raise RuntimeError(f"Brief stage failed: {output}")
        _ensure_brief_persisted(parsed)
        return

    # Not JSON. Before treating this as a failure, check whether the
    # agent's own tool calls already saved everything correctly.
    if _tool_call_succeeded(result, "Row appended to content_briefs"):
        print("[brief] Final answer wasn't JSON, but content_briefs was already updated via a real tool call -- treating as success.")
        return
    if _looks_like_unexecuted_tool_call(output):
        raise RuntimeError(
            "Brief stage failed: model returned an unexecuted tool call instead of a brief "
            f"(no content to salvage) -- retry the stage. Raw: {output}"
        )
    raise RuntimeError(f"Brief stage reported success but produced unparseable output: {output[:300]}")


_TITLE_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*\n", re.MULTILINE)
_FAQ_JSON_RE = re.compile(r"\[\s*\{.*?\"question\".*?\}\s*\]", re.DOTALL)
_FAQ_LABEL_RE = re.compile(r"^(?:#{2,3}\s*FAQs?|\*\*FAQs?\*\*)\s*$", re.MULTILINE | re.IGNORECASE)
_SECTION_BREAK_RE = re.compile(r"^(?:#{2,3}\s+.+|\*\*[A-Z][a-zA-Z ]*\*\*:?)\s*$", re.MULTILINE)
_FAQ_PAIR_RE = re.compile(r"\*\*(.+?)\*\*\s*\n(.+?)(?=\n\*\*|\Z)", re.DOTALL)
_SUMMARY_LABEL_RE = re.compile(r"^\*\*(?:Meta Description|Summary)\*\*:?\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_TRAILING_RULE_RE = re.compile(r"\n-{3,}\s*\n")


def _extract_content_from_markdown(output: str) -> Optional[dict]:
    """Salvage a full blog post when a fallback model skips the required
    JSON envelope entirely and just returns raw Markdown instead -- confirmed
    live across multiple runs, each in a different shape (title as "#" vs
    "##", FAQs as a "## FAQs" heading with **Q**/A pairs vs a "**FAQs**"
    label followed by a raw JSON array, an optional "**Summary**: ..."
    line). Discarding a fully-written, well-researched post and forcing a
    full regeneration would waste more of an already-scarce fallback-provider
    quota for nothing, so this tries to recover the same fields the JSON
    envelope would have contained regardless of which shape came back.
    Output that isn't actually a real post (too short, no title) still falls
    through to a genuine failure."""
    text = output.strip()
    if not text.startswith("#"):
        return None

    title_match = _TITLE_HEADING_RE.match(text)
    if not title_match:
        return None
    title = title_match.group(1).strip()
    body = text[title_match.end():]

    summary = ""
    summary_match = _SUMMARY_LABEL_RE.search(body)
    if summary_match:
        summary = summary_match.group(1).strip()
        body = body[:summary_match.start()] + body[summary_match.end():]

    # FAQs: prefer a raw JSON array embedded directly in the text (more
    # reliable to parse than Markdown Q&A pairs) if present; fall back to a
    # "## FAQs"/"**FAQs**" label followed by **Question** / answer pairs.
    faqs = []
    json_match = _FAQ_JSON_RE.search(body)
    if json_match:
        try:
            candidate = json.loads(json_match.group(0))
            if isinstance(candidate, list) and all(
                isinstance(item, dict) and "question" in item and "answer" in item for item in candidate
            ):
                faqs = candidate
                body = body[:json_match.start()] + body[json_match.end():]
        except (json.JSONDecodeError, TypeError):
            pass

    faq_label_match = _FAQ_LABEL_RE.search(body)
    if faq_label_match:
        if faqs:
            # JSON array already extracted -- just drop the leftover label line.
            body = body[:faq_label_match.start()] + body[faq_label_match.end():]
        else:
            section_start = faq_label_match.end()
            next_section_match = _SECTION_BREAK_RE.search(body, section_start)
            section_end = next_section_match.start() if next_section_match else len(body)
            for question, answer in _FAQ_PAIR_RE.findall(body[section_start:section_end]):
                question = question.strip().strip("*").strip()
                answer = " ".join(answer.strip().splitlines()).strip()
                if question and answer:
                    faqs.append({"question": question, "answer": answer})
            body = body[:faq_label_match.start()] + body[section_end:]

    body = _TRAILING_RULE_RE.sub("\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    if not title or len(body) < 200:
        return None

    if not summary:
        # No separate meta description in this shape -- derive a plausible
        # one from the opening paragraph rather than leaving it empty.
        plain_intro = re.sub(r"^#{2,3}\s+.+$", "", body, count=1, flags=re.MULTILINE)
        plain_intro = re.sub(r"[#*_`\[\]()]", "", plain_intro).strip()
        plain_intro = re.sub(r"\s+", " ", plain_intro)
        summary = plain_intro[:160].rsplit(" ", 1)[0] if len(plain_intro) > 160 else plain_intro

    return {
        "status": "success",
        "Title": title,
        "Generated Content": body,
        "FAQs": faqs,
        "Summary": summary,
        "Quality Score": "",
        "Approve/Disapprove": "Approved",
        "Published": "No",
    }


def _looks_like_unexecuted_tool_call(output: str) -> bool:
    """Detects a fallback model's OpenAI-compatibility layer leaking a tool
    call it never actually executed as plain-text JSON (e.g. a list of
    {"tool_call_id", "tool_name", "parameters"} objects) instead of running
    tavily_search_tool/etc. through the SDK's real function-calling
    protocol (confirmed live, previously via Cohere before it was removed
    as a provider -- kept as a general check since any OpenAI-compatibility
    shim could in principle do the same). There's no content to salvage
    here -- unlike a missing JSON envelope, this genuinely has no post in
    it -- but flagging the pattern explicitly makes the failure instantly
    recognizable instead of just dumping the raw JSON as an opaque error."""
    try:
        parsed = json.loads(output.strip())
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, list) and bool(parsed) and all(
        isinstance(item, dict) and "tool_name" in item for item in parsed
    )


_GENERATED_POSTS_FIELDS = ["Title", "Generated Content", "FAQs", "Quality Score", "Summary", "Approve/Disapprove", "Published"]


def _content_row_values(content: dict, title: str) -> dict:
    faqs = _get_field(content, "FAQs", [])
    faqs_str = faqs if isinstance(faqs, str) else json.dumps(faqs)
    return {
        "Title": title,
        "Generated Content": str(_get_field(content, "Generated Content")),
        "FAQs": faqs_str,
        "Quality Score": str(_get_field(content, "Quality Score")),
        "Summary": str(_get_field(content, "Summary")),
        "Approve/Disapprove": str(_get_field(content, "Approve/Disapprove", "Approved")),
        "Published": str(_get_field(content, "Published", "No")),
    }


def _row_looks_malformed(existing_row: dict) -> bool:
    """Detects a shifted-columns row: confirmed live, a generated_posts row
    had "Yes" sitting in the Summary column with Published itself empty --
    the Content Generator Agent's own append_row tool call passed too few
    values (skipping Summary and/or Approve/Disapprove), and since
    append_row just writes whatever list it's given positionally, gspread
    mapped everything after the gap one or more columns to the left. A
    meta-description-shaped Summary should never literally be "yes"/"no",
    and Published should never be empty once a row exists."""
    summary_value = str(_get_field(existing_row, "Summary", "")).strip().lower()
    published_value = str(_get_field(existing_row, "Published", "")).strip()
    return summary_value in ("yes", "no") or not published_value


def _ensure_content_persisted(content: dict) -> dict:
    """Same fix as _ensure_brief_persisted, for the Content Generator Agent
    -> generated_posts. Returns the persisted row (existing, repaired, or
    freshly appended) so the caller can use it directly for the Discord
    notification instead of blindly trusting "last row = the one just
    generated", which would silently notify about a stale row if the agent
    hadn't actually saved anything."""
    title = str(_get_field(content, "Title")).strip()
    if not title:
        raise RuntimeError(f"Content output missing Title; cannot persist or verify. Keys seen: {list(content.keys())}")

    existing = manage_sheet_data(worksheet_name="generated_posts", action="get_all_records")
    existing_row = None
    existing_row_index = None
    if existing.get("status") == "success":
        for i, row in enumerate(existing.get("data", []), start=2):  # row 1 is the header
            if str(_get_field(row, "Title")).strip() == title:
                existing_row = row
                existing_row_index = i

    if existing_row:
        if not _row_looks_malformed(existing_row):
            print(f"[content] generated_posts already has a row for '{title}'; agent saved it correctly.")
            return existing_row

        # The agent's own append_row call shipped the wrong shape (too few
        # values, columns shifted). manage_sheet_data_tool can't validate
        # the semantic correctness of a list an agent hands it -- it just
        # writes what it's given. Repair the row by column *name* instead
        # of appending a duplicate.
        print(f"[content] generated_posts row for '{title}' looks malformed (Summary={_get_field(existing_row, 'Summary', '')!r}, Published={_get_field(existing_row, 'Published', '')!r}) -- repairing by column name.")
        correct_values = _content_row_values(content, title)
        headers_result = manage_sheet_data(worksheet_name="generated_posts", action="get_range", cell_range="1:1")
        header_row = (headers_result.get("data") or [[]])[0] if headers_result.get("status") == "success" else _GENERATED_POSTS_FIELDS
        for field_name, value in correct_values.items():
            if field_name not in header_row:
                continue
            col_index = header_row.index(field_name) + 1
            update_result = manage_sheet_data(
                worksheet_name="generated_posts", action="update_cell",
                row_index=existing_row_index, col_index=col_index, data=value,
            )
            if update_result.get("status") != "success":
                print(f"[content] Warning: failed to repair '{field_name}' for '{title}': {update_result}")
        print(f"[content] Repaired generated_posts row {existing_row_index} for '{title}'.")
        return correct_values

    row_values = _content_row_values(content, title)
    append_result = manage_sheet_data(
        worksheet_name="generated_posts",
        action="append_row",
        row_values=list(row_values.values()),
    )
    if append_result.get("status") != "success":
        raise RuntimeError(f"Failed to persist content to generated_posts: {append_result}")
    print(f"[content] Appended row to generated_posts for '{title}' (agent reported success but had not saved it).")
    return row_values


async def run_content() -> None:
    result = await custom_runner.run_with_fallback(
        content_generator_agent,
        "Generate content based on the first available brief that is not generated yet from the content_briefs worksheet and add it to the generated_posts worksheet.",
        max_turns=MAX_TURNS,
    )
    output = str(getattr(result, "final_output", result))
    print(f"[content] result: {output}")

    if _is_benign_empty(output):
        return

    parsed = _parse_agent_json(output)
    if parsed is not None:
        if _get_field(parsed, "status") == "error":
            raise RuntimeError(f"Content stage failed: {output}")
    else:
        # Not JSON at all -- try to salvage a raw Markdown post before
        # treating this as a genuine failure. Confirmed live: a fallback
        # model sometimes skips the documented JSON envelope entirely and
        # just returns the finished post as plain text.
        parsed = _extract_content_from_markdown(output)
        if parsed is None:
            # Still not salvageable as text -- before giving up, check
            # whether the agent's own tool calls already saved everything
            # correctly (confirmed live for the brief stage: a model can
            # execute append_row successfully and then just summarize the
            # result as a plain sentence with nothing to parse at all).
            if _tool_call_succeeded(result, "Row appended to generated_posts"):
                print("[content] Final answer wasn't JSON/Markdown, but generated_posts was already updated via a real tool call -- reading back the saved row.")
                records = manage_sheet_data(worksheet_name="generated_posts", action="get_all_records")
                if records.get("status") == "success" and records.get("data"):
                    last_row = records["data"][-1]
                    _notify_discord(
                        title=last_row.get("Title", "Untitled"),
                        summary=last_row.get("Summary", ""),
                        content=last_row.get("Generated Content", ""),
                        faqs=last_row.get("FAQs", ""),
                    )
                return
            if _looks_like_unexecuted_tool_call(output):
                raise RuntimeError(
                    "Content stage failed: model returned an unexecuted tool call instead of "
                    f"content (no post to salvage) -- retry the stage. Raw: {output}"
                )
            raise RuntimeError(f"Content stage failed: {output}")
        print("[content] Agent returned raw Markdown instead of the JSON envelope; salvaged it instead of discarding a completed post.")

    persisted_row = _ensure_content_persisted(parsed)
    _notify_discord(
        title=persisted_row.get("Title", "Untitled"),
        summary=persisted_row.get("Summary", ""),
        content=persisted_row.get("Generated Content", ""),
        faqs=persisted_row.get("FAQs", ""),
    )


async def run_post() -> None:
    result = await run_posting_workflow()
    print(f"[post] result: {result}")
    if isinstance(result, dict) and result.get("status") == "error":
        raise RuntimeError(f"Posting stage failed: {result.get('error')}")

    if isinstance(result, dict) and result.get("post_url"):
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if webhook_url:
            title = result.get("title") or "Untitled"
            content = f"🌐 Published **{title}**\n{result['post_url']}"
            try:
                _post_discord_message(webhook_url, content)
            except Exception as e:
                print(f"Failed to send Discord publish notification: {e}")


STAGE_HANDLERS = {
    "research": run_research,
    "brief": run_brief,
    "content": run_content,
    "post": run_post,
    "discover_topics": run_discover_topics,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a single ContentSpark pipeline stage.")
    parser.add_argument("--stage", required=True, choices=sorted(STAGE_HANDLERS))
    args = parser.parse_args()

    try:
        asyncio.run(STAGE_HANDLERS[args.stage]())
    except Exception as e:
        print(f"Stage '{args.stage}' failed: {e}", file=sys.stderr)
        _notify_discord_status(args.stage, success=False, detail=str(e))
        sys.exit(1)
    else:
        # "content" and "post" already get their own richer notifications
        # (draft-ready / published-with-URL); a generic success ping on top
        # would just be noise.
        if args.stage not in ("content", "post"):
            _notify_discord_status(args.stage, success=True)


if __name__ == "__main__":
    main()
