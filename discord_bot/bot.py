"""
Discord bot for ContentSpark AI.

Four responsibilities:
  1. Approvals -- listens for checkmark/cross reactions on draft-preview
     messages (posted by scripts/run_stage.py's Discord webhook call after the
     "content" stage) and writes the result into the Approve/Disapprove column
     of the generated_posts worksheet. Also handles reactions on trending-topic
     candidate messages (posted by the "discover_topics" stage), adding an
     approved candidate to ContentSpark_Keywords.
  2. Triggering -- a /run slash command that dispatches pipeline.yml via
     GitHub's workflow_dispatch API, for on-demand runs.
  3. Queueing -- a /add_topic slash command that appends a new row to
     ContentSpark_Keywords (Status=available) from a short concept/problem
     statement or an uploaded .txt transcript, for the Triage Agent to pick
     up on the next research run.
  4. Status/chat -- a /status slash command for a quick deterministic count of
     what's queued/pending at each pipeline stage, and conversational replies
     (via DeepSeek V4 Flash on OpenRouter) when the bot is @mentioned, so you
     can ask things like "how many briefs are waiting" or discuss a topic idea
     without leaving Discord.

Talks to Google Sheets directly via gspread rather than importing
tools/sheet_tool.py from the main pipeline -- that module's @function_tool
decorators would otherwise couple this bot's deploy to changes in the
pipeline's tools/ code even though they deploy independently (see
.github/workflows/deploy-bot.yml's path filter). The chat feature does use
the openai-agents SDK directly (same package as the pipeline, pinned to the
same version) since a real Agent -- with its own tool-calling loop -- is
what makes "ask the bot about pipeline status" actually work instead of a
single stateless completion call; the SDK dependency was worth taking on
for that, but the bot still authors its own small tool set rather than
importing the pipeline's.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import discord
import gspread
import requests
from agents import (
    Agent,
    AgentHooks,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    Tool,
    function_tool,
    set_tracing_disabled,
)
from discord import app_commands
from discord.ext import commands, tasks
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.service_account import Credentials

# Not using OpenAI's own API for inference (OpenRouter/DeepSeek instead), so
# tracing export is disabled -- same rationale as the main pipeline's
# scripts/run_stage.py.
set_tracing_disabled(True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("contentspark-bot")

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GITHUB_PAT = os.environ["GITHUB_PAT"]
# Expected as "owner/repo", but a full GitHub URL is an easy mistake to paste
# into a Dokploy env field -- normalize it instead of hard-failing on it.
GITHUB_REPO = (
    os.environ["GITHUB_REPO"]
    .strip()
    .removeprefix("https://github.com/")
    .removeprefix("http://github.com/")
    .removesuffix(".git")
    .strip("/")
)
APPROVAL_CHANNEL_ID = int(os.environ["DISCORD_APPROVAL_CHANNEL_ID"])
# Optional: powers the conversational /status follow-up chat. If unset, /status
# still works (it's pure sheet reads, no LLM needed) but @mentioning the bot
# just explains that chat isn't configured instead of silently doing nothing.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
# OpenRouter uses a literal "~" prefix on a model slug to mean "always
# resolve to the latest version of this model family" -- without it,
# "deepseek/deepseek-v4-flash-latest" 400s as "not a valid model ID"
# (confirmed live: real error, real OpenRouter docs screenshot).
DEEPSEEK_MODEL = "~deepseek/deepseek-v4-flash-latest"

SHEET_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
# Same GOOGLE_CREDENTIALS service account as SHEET_SCOPE above, just
# requested with a different OAuth scope for a different Google API --
# reused deliberately rather than a second credential, per
# docs/service_setup.md's Search Console setup section.
SEARCH_CONSOLE_SCOPE = ["https://www.googleapis.com/auth/webmasters.readonly"]
SEARCH_CONSOLE_SITE_URL = "https://owaisabdullah.dev/"
SPREADSHEET_NAME = "ContentSpark"
WORKSHEET_NAME = "generated_posts"
# Title, Generated Content, FAQs, Quality Score, Summary, Approve/Disapprove, Published
APPROVE_DISAPPROVE_COLUMN = 6

# ContentSpark_Keywords is a separate spreadsheet file (not a worksheet
# inside ContentSpark) -- see docs/service_setup.md section 2b. get_keyword_tool
# reads its first sheet, expecting columns: Keyword, Status.
KEYWORDS_SPREADSHEET_NAME = "ContentSpark_Keywords"

# Google Sheets caps a single cell at 50,000 characters. Leave headroom
# rather than hitting that exactly.
MAX_KEYWORD_CELL_LENGTH = 49000

# "edit_post" is a valid pipeline.yml stage but deliberately excluded here --
# it requires edit_title/edit_instruction inputs that neither /run nor
# trigger_stage_tool collect. It's only reachable via edit_post_content_tool,
# which gathers those and dispatches the workflow directly.
STAGE_CHOICES = ["research", "brief", "content", "post", "discover_topics"]

# Discord's gateway sends heartbeat-related traffic roughly every ~41s by
# default, so 120s of complete silence on the socket is a strong "connection
# is stale/dead" signal even though discord.py still thinks it's connected --
# the same class of bug OpenClaw's own Discord integration has repeatedly hit
# (gateway reports connected but no events ever arrive again). Rather than
# try to force a resume from inside a running process, the watchdog below
# does what OpenClaw's health-monitor does: log it and restart. The process
# exits non-zero and Dokploy's restart-always policy brings up a fresh
# process with a fresh gateway connection.
STALE_CONNECTION_SECONDS = 120
WATCHDOG_INTERVAL_SECONDS = 30

# discord.py's tasks.loop has no native "every Monday at 9am" scheduling, so
# this is a rolling 168-hour interval from whenever the bot process last
# started, not a fixed calendar day/time. A restart (e.g. the connection
# watchdog above firing) resets the timer -- acceptable for a nice-to-have
# pulse-check digest, not worth persisting state across restarts for.
WEEKLY_DIGEST_INTERVAL_HOURS = 168

_gspread_client = None


def _get_gspread_client():
    """Cached, same rationale as tools/sheet_tool.py's client caching:
    google-auth Credentials refresh their own tokens, so it's safe to reuse
    one authorized client across events instead of re-authenticating on every
    reaction/command."""
    global _gspread_client
    if _gspread_client is None:
        creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
        creds = Credentials.from_service_account_info(creds_info, scopes=SHEET_SCOPE)
        _gspread_client = gspread.authorize(creds)
    return _gspread_client


def _get_content_spark_worksheet(worksheet_name: str):
    return _get_gspread_client().open(SPREADSHEET_NAME).worksheet(worksheet_name)


_search_console_creds: Optional[Credentials] = None


def _get_search_console_totals(days: int = 7) -> Optional[dict]:
    """Site-wide clicks/impressions/avg position over a trailing window, via
    a raw Search Console REST call -- own small copy rather than importing
    lib/search_console.py from the main pipeline, matching this bot's
    established pattern (see the module docstring) of not depending on
    pipeline-specific modules the Docker build context (discord_bot/ only)
    can't even see. Returns None on any failure or if there's no data yet --
    a Search Console hiccup shouldn't break the whole digest."""
    global _search_console_creds
    try:
        if _search_console_creds is None:
            creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
            _search_console_creds = Credentials.from_service_account_info(creds_info, scopes=SEARCH_CONSOLE_SCOPE)
        if not _search_console_creds.valid:
            _search_console_creds.refresh(GoogleAuthRequest())

        end = (datetime.now(timezone.utc) - timedelta(days=3)).date()  # GSC has a ~2-3 day lag
        start = end - timedelta(days=days)
        response = requests.post(
            f"https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(SEARCH_CONSOLE_SITE_URL, safe='')}/searchAnalytics/query",
            headers={"Authorization": f"Bearer {_search_console_creds.token}"},
            json={"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": 1},
            timeout=15,
        )
        response.raise_for_status()
        rows = response.json().get("rows", [])
        if not rows:
            return None
        row = rows[0]
        return {
            "clicks": row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "position": row.get("position", 0.0),
        }
    except Exception as e:
        logger.warning(f"_get_search_console_totals: failed: {e}")
        return None


def _get_worksheet():
    return _get_content_spark_worksheet(WORKSHEET_NAME)


def _get_keywords_worksheet():
    return _get_gspread_client().open(KEYWORDS_SPREADSHEET_NAME).sheet1


def set_approval(title: str, status: str) -> bool:
    """Finds the row with the given Title in generated_posts and sets its
    Approve/Disapprove cell. Returns True if a matching row was found."""
    worksheet = _get_worksheet()
    records = worksheet.get_all_records()
    for i, record in enumerate(records, start=2):  # row 1 is the header
        if str(record.get("Title", "")).strip() == title.strip():
            worksheet.update_cell(i, APPROVE_DISAPPROVE_COLUMN, status)
            return True
    return False


def add_keyword(content: str) -> None:
    """Appends a new row to ContentSpark_Keywords with Status=available, so
    the Triage Agent picks it up on the next research run. Matches the
    columns get_keyword_tool (tools/sheet_tool.py) expects: Keyword, Status."""
    worksheet = _get_keywords_worksheet()
    worksheet.append_row([content, "available"])


async def _check_duplicate_topic(candidate: str) -> Optional[str]:
    """Compares candidate against every topic already queued, researched, or
    published, and returns the matching existing topic text if it looks like
    the same underlying story (not just the same general subject), or None
    if it's sufficiently distinct or the check can't run. Nothing currently
    stops discover_topics -- which pulls fresh Reddit/Quora trending
    discussion every ~2 days -- from proposing essentially the same story
    twice, wasting a full research->brief->content cycle. This is
    deliberately advisory, never blocking: it only adds a warning to the
    confirmation message, the human approving the topic is still the final
    call, same as every other decision point in this bot."""
    if not OPENROUTER_API_KEY:
        return None
    try:
        existing = []
        existing += [str(r.get("Keyword", "")).strip() for r in _get_keywords_worksheet().get_all_records()]
        existing += [str(r.get("Keyword/Topic", "")).strip() for r in _get_content_spark_worksheet("research_data").get_all_records()]
        existing += [str(r.get("Keyword/Topic", "")).strip() for r in _get_content_spark_worksheet("content_briefs").get_all_records()]
        existing += [str(r.get("Title", "")).strip() for r in _get_worksheet().get_all_records()]
        existing += [str(r.get("Keyword/Topic", "")).strip() for r in _get_content_spark_worksheet("published_posts").get_all_records()]
        existing = [e for e in existing if e]
    except Exception as e:
        logger.warning(f"_check_duplicate_topic: failed to gather existing topics: {e}")
        return None
    if not existing:
        return None
    # Cap the prompt size -- these sheets grow to hundreds of rows over time,
    # and this only needs to catch genuinely recent overlap, not every topic
    # ever queued.
    existing = existing[-300:]

    client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    prompt = (
        "Candidate topic:\n" + candidate + "\n\n"
        "Existing topics already queued, researched, or published (one per line):\n"
        + "\n".join(f"- {t}" for t in existing) + "\n\n"
        "Does the candidate cover essentially the SAME specific story/topic as any one of "
        "these -- not just the same general subject area (e.g. two different posts about "
        "\"AI agents\" broadly are NOT duplicates, but two posts about the same specific "
        "product's same specific release ARE)? Reply with ONLY the exact matching existing "
        "topic text if yes, or the single word NONE if no close match exists. No other text."
    )
    try:
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        answer = (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"_check_duplicate_topic: LLM check failed: {e}")
        return None
    if not answer or answer.strip().upper() == "NONE":
        return None
    return answer


# Cap how many sample titles get pulled into a status report / chat prompt --
# these sheets can grow to hundreds of rows, and this is a glanceable summary,
# not a full export.
MAX_SAMPLE_ITEMS = 5


def gather_pipeline_status() -> dict:
    """Reads every stage's worksheet and returns counts + a few sample titles
    for what's currently queued/pending at each step. Used by both /status
    (direct) and the @mention chat (as live context fed to the LLM) so the
    two never disagree with each other."""
    status = {}

    try:
        keyword_records = _get_keywords_worksheet().get_all_records()
        available = [r for r in keyword_records if str(r.get("Status", "")).strip().lower() == "available"]
        status["keywords_available"] = len(available)
        status["keyword_samples"] = [str(r.get("Keyword", "")).strip() for r in available[:MAX_SAMPLE_ITEMS]]
    except Exception as e:
        logger.warning(f"gather_pipeline_status: failed to read ContentSpark_Keywords: {e}")
        status["keywords_available"] = None

    try:
        research_records = _get_content_spark_worksheet("research_data").get_all_records()
        ungenerated = [r for r in research_records if str(r.get("Generated", "")).strip().lower() != "yes"]
        status["research_pending_brief"] = len(ungenerated)
        status["research_pending_samples"] = [str(r.get("Keyword/Topic", "")).strip() for r in ungenerated[:MAX_SAMPLE_ITEMS]]
    except Exception as e:
        logger.warning(f"gather_pipeline_status: failed to read research_data: {e}")
        status["research_pending_brief"] = None

    try:
        brief_records = _get_content_spark_worksheet("content_briefs").get_all_records()
        ungenerated = [r for r in brief_records if str(r.get("Generated", "")).strip().lower() != "yes"]
        status["briefs_pending_content"] = len(ungenerated)
        status["brief_samples"] = [str(r.get("Keyword/Topic", "")).strip() for r in ungenerated[:MAX_SAMPLE_ITEMS]]
    except Exception as e:
        logger.warning(f"gather_pipeline_status: failed to read content_briefs: {e}")
        status["briefs_pending_content"] = None

    try:
        post_records = _get_worksheet().get_all_records()  # generated_posts
        pending_review = [r for r in post_records if not str(r.get("Approve/Disapprove", "")).strip()]
        approved_unpublished = [
            r for r in post_records
            if str(r.get("Approve/Disapprove", "")).strip().lower() == "approved"
            and str(r.get("Published", "")).strip().lower() != "yes"
        ]
        status["posts_pending_review"] = len(pending_review)
        status["posts_approved_unpublished"] = len(approved_unpublished)
        status["posts_total_generated"] = len(post_records)
        status["pending_review_samples"] = [str(r.get("Title", "")).strip() for r in pending_review[:MAX_SAMPLE_ITEMS]]
        # Full titles, not just a capped sample -- this is exactly the "what
        # are the approved-but-unpublished titles" question the bot couldn't
        # previously answer (it only had the count).
        status["approved_unpublished_titles"] = [str(r.get("Title", "")).strip() for r in approved_unpublished]
    except Exception as e:
        logger.warning(f"gather_pipeline_status: failed to read generated_posts: {e}")
        status["posts_pending_review"] = None
        status["posts_approved_unpublished"] = None
        status["posts_total_generated"] = None
        status["approved_unpublished_titles"] = None

    try:
        published_records = _get_content_spark_worksheet("published_posts").get_all_records()
        # posting_agent.py's own instructions add a row here on EVERY publish
        # attempt, not just successful ones -- "Error: empty string if
        # successful" implies a populated Error value on failure rather than
        # skipping the row. Confirmed live: this sheet's row count (37) was
        # well ahead of the live sitemap's actual post count (23), and
        # historical duplicate-Sanity-publish attempts (see
        # docs/fixes_and_improvements.md) would each have logged their own
        # row too, before that was fixed. Count only rows with an empty
        # Error column so this reflects actually-published posts.
        published_records = [r for r in published_records if not str(r.get("Error", "")).strip()]
        status["posts_published_total"] = len(published_records)
    except Exception as e:
        logger.warning(f"gather_pipeline_status: failed to read published_posts: {e}")
        status["posts_published_total"] = None

    return status


def format_status_report(status: dict) -> str:
    def n(key):
        value = status.get(key)
        return "?" if value is None else str(value)

    lines = [
        "**ContentSpark Pipeline Status**",
        f"📥 Keywords queued: **{n('keywords_available')}**",
        f"🔍 Research pending a brief: **{n('research_pending_brief')}**",
        f"📝 Briefs pending content: **{n('briefs_pending_content')}**",
        f"👀 Posts awaiting your review: **{n('posts_pending_review')}**",
        f"✅ Approved, not yet published: **{n('posts_approved_unpublished')}**",
        f"📚 Total generated posts: **{n('posts_total_generated')}**",
        f"🌐 Total published: **{n('posts_published_total')}**",
    ]
    if status.get("keyword_samples"):
        lines.append("\n**Queued keywords:** " + ", ".join(status["keyword_samples"]))
    if status.get("research_pending_samples"):
        lines.append("**Researched, pending brief:** " + ", ".join(status["research_pending_samples"]))
    if status.get("brief_samples"):
        lines.append("**Briefed, pending content:** " + ", ".join(status["brief_samples"]))
    if status.get("pending_review_samples"):
        lines.append("**Awaiting review:** " + ", ".join(status["pending_review_samples"]))
    if status.get("approved_unpublished_titles"):
        lines.append("**Approved, ready to publish:** " + ", ".join(status["approved_unpublished_titles"]))
    return "\n".join(lines)


def _rows_created_since(worksheet_name: str, cutoff: datetime) -> List[dict]:
    """Filters a worksheet's rows to ones stamped with a "Created At" at or
    after cutoff (see scripts/run_stage.py::_stamp_created_at, which writes
    that column). Rows without a parseable timestamp (predating that column,
    or a stamp failure) are excluded rather than guessed at."""
    try:
        records = _get_content_spark_worksheet(worksheet_name).get_all_records()
    except Exception as e:
        logger.warning(f"_rows_created_since: failed to read {worksheet_name}: {e}")
        return []
    matched = []
    for r in records:
        raw = str(r.get("Created At", "")).strip()
        if not raw:
            continue
        try:
            stamped = datetime.strptime(raw, _CREATED_AT_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if stamped >= cutoff:
            matched.append(r)
    return matched


async def _count_recent_stage_failures(channel, since: datetime) -> Optional[int]:
    """Counts "Stage X failed" notifications posted to the approval channel
    since the given cutoff -- the only record of stage failures this bot has
    access to (scripts/run_stage.py posts them there via
    _notify_discord_status, but doesn't persist them anywhere queryable).
    Returns None if the channel history can't be read, so the digest can
    say "couldn't check" instead of implying zero failures."""
    try:
        count = 0
        async for message in channel.history(after=since, limit=1000):
            if message.author.bot and "Stage" in message.content and "failed" in message.content:
                count += 1
        return count
    except Exception as e:
        logger.warning(f"_count_recent_stage_failures: failed to read channel history: {e}")
        return None


async def _build_weekly_digest(channel) -> str:
    """A weekly pulse-check summarizing what actually happened, not just
    current totals (that's what /status is for): posts generated and their
    average quality score, briefs written, and stage failures, all in the
    last 7 days -- using the "Created At" column (this session's own
    addition) rather than guessing from row position."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    lines = ["**📅 Weekly ContentSpark Digest** (last 7 days)"]

    posts_this_week = _rows_created_since("generated_posts", week_ago)
    if posts_this_week:
        scores = []
        for r in posts_this_week:
            try:
                scores.append(float(str(r.get("Quality Score", "")).strip()))
            except ValueError:
                continue
        avg_score = f"{sum(scores) / len(scores):.0f}" if scores else "?"
        lines.append(f"✍️ Posts generated: **{len(posts_this_week)}** (avg quality score: **{avg_score}**)")
    else:
        lines.append("✍️ Posts generated: **0**")

    briefs_this_week = _rows_created_since("content_briefs", week_ago)
    lines.append(f"📝 Briefs written: **{len(briefs_this_week)}**")

    try:
        published_records = _get_content_spark_worksheet("published_posts").get_all_records()
        published_total = len([r for r in published_records if not str(r.get("Error", "")).strip()])
        lines.append(f"🌐 Total published (all time): **{published_total}**")
    except Exception as e:
        logger.warning(f"_build_weekly_digest: failed to read published_posts: {e}")

    failures = await _count_recent_stage_failures(channel, week_ago)
    if failures is None:
        lines.append("⚠️ Stage failures this week: couldn't check channel history")
    elif failures > 0:
        lines.append(f"⚠️ Stage failures this week: **{failures}** -- check the Actions tab for details")
    else:
        lines.append("✅ No stage failures this week")

    search_console = _get_search_console_totals(days=7)
    if search_console:
        lines.append(
            f"🔍 Search performance: **{search_console['clicks']:.0f} clicks**, "
            f"**{search_console['impressions']:.0f} impressions**, "
            f"avg position **{search_console['position']:.1f}**"
        )
    else:
        lines.append("🔍 Search performance: no data available this week")

    return "\n".join(lines)


@function_tool
def get_pipeline_status_tool() -> dict:
    """Returns live counts of what's queued/pending at each ContentSpark
    pipeline stage: keywords queued, research pending a brief, briefs
    pending content, posts awaiting review, approved-but-unpublished posts,
    total generated posts, and total published posts. Also returns actual
    titles, not just counts: keyword_samples, research_pending_samples,
    brief_samples, pending_review_samples (all capped samples), and
    approved_unpublished_titles (the FULL list of approved-but-unpublished
    post titles, not capped -- use this to answer "what are the approved
    posts titled" instead of saying you can't see them). Call this whenever
    the question is about counts, what's queued, what's currently pending,
    or which specific topics/posts are at a given stage; don't guess."""
    return gather_pipeline_status()


def _find_row_index(worksheet, key_column: str, needle: str) -> Optional[int]:
    """Case-insensitive substring match against key_column. Returns the
    1-based row index of the first match, or None."""
    records = worksheet.get_all_records()
    needle = needle.strip().lower()
    for i, record in enumerate(records, start=2):  # row 1 is the header
        if needle in str(record.get(key_column, "")).strip().lower():
            return i
    return None


def _move_row_to_top(worksheet, row_index: int) -> None:
    """Reorders a row to be first in the data (row 2, right after the
    header) without deleting or losing anything -- delete + re-insert
    preserves the row's own values, it just changes position, so the next
    pipeline run (which always picks the first eligible row) picks this one
    up next instead of whatever happened to be queued earlier."""
    row_values = worksheet.row_values(row_index)
    worksheet.delete_rows(row_index)
    worksheet.insert_row(row_values, 2, value_input_option="USER_ENTERED")


# (worksheet getter, key column, label, the stage that picks this queue up next)
_PRIORITIZABLE_QUEUES = [
    (lambda: _get_keywords_worksheet(), "Keyword", "ContentSpark_Keywords", "research"),
    (lambda: _get_content_spark_worksheet("research_data"), "Keyword/Topic", "research_data", "brief"),
    (lambda: _get_content_spark_worksheet("content_briefs"), "Keyword/Topic", "content_briefs", "content"),
    # generated_posts: the `post` stage's Preparation Agent always reads
    # approved_unpublished row 2, a filtered view of generated_posts that
    # preserves generated_posts' own row order -- moving a post to the top
    # here moves it to the top of that view too, so it's next in line to
    # actually get published. Matches by Title regardless of its current
    # Approve/Disapprove or Published value (same as the other queues, this
    # only reorders, never filters or deletes).
    (lambda: _get_worksheet(), "Title", "generated_posts", "post"),
]


@function_tool
def prioritize_topic_tool(topic_reference: str) -> dict:
    """Moves the row matching topic_reference (case-insensitive, partial
    match is fine -- e.g. "digital fte" matches "Digital FTE: ...") to the
    top of whichever queue it's currently sitting in: ContentSpark_Keywords
    (queued, not yet researched), research_data (researched, pending a
    brief), or content_briefs (has a brief, pending content). Does NOT
    delete or remove anything else in that queue, only reorders, so the
    next pipeline run for that stage picks this topic up first instead of
    whatever was ahead of it. Searches in that stage order and acts on the
    first match found. Call this when the user asks to prioritize, bump, or
    run a specific topic they name. Returns which queue it was found in and
    which stage to trigger next (pass that to trigger_stage_tool)."""
    for get_worksheet, key_column, label, next_stage in _PRIORITIZABLE_QUEUES:
        try:
            worksheet = get_worksheet()
            row_index = _find_row_index(worksheet, key_column, topic_reference)
        except Exception as e:
            logger.warning(f"prioritize_topic_tool: failed to search {label}: {e}")
            continue
        if row_index is None:
            continue
        if row_index == 2:
            return {"found": True, "worksheet": label, "already_first": True, "next_stage": next_stage}
        try:
            _move_row_to_top(worksheet, row_index)
        except Exception as e:
            logger.exception(f"prioritize_topic_tool: failed to reorder {label}")
            return {"found": True, "worksheet": label, "error": str(e)}
        return {"found": True, "worksheet": label, "moved_to_top": True, "next_stage": next_stage}
    return {"found": False, "message": f"No queued/pending item matching '{topic_reference}' found."}


@function_tool
def trigger_stage_tool(stage: str) -> dict:
    """Triggers a ContentSpark pipeline stage run via GitHub Actions --
    the same effect as the /run slash command. Valid stages: research,
    brief, content, post, discover_topics. Use this after
    prioritize_topic_tool to actually advance a prioritized topic, or
    whenever the user asks in chat to run/trigger a specific stage. The
    result includes queued_behind_another_run: if true, tell the user this
    run will start once the currently-active run finishes, not immediately
    -- don't imply it's running right now."""
    if stage not in STAGE_CHOICES:
        return {"status": "error", "error": f"'{stage}' is not a valid stage. Valid: {STAGE_CHOICES}"}
    try:
        already_running = dispatch_workflow(stage)
    except Exception as e:
        logger.exception(f"trigger_stage_tool: failed to dispatch {stage}")
        return {"status": "error", "error": str(e)}
    return {"status": "triggered", "stage": stage, "queued_behind_another_run": already_running}


# Title, Generated Content, FAQs, Quality Score, Summary, Approve/Disapprove, Published
PUBLISHED_COLUMN = 7


def _update_generated_posts_column(title_reference: str, col_index: int, value: str) -> dict:
    worksheet = _get_worksheet()
    row_index = _find_row_index(worksheet, "Title", title_reference)
    if row_index is None:
        return {"found": False, "message": f"No post matching '{title_reference}' found in generated_posts."}
    worksheet.update_cell(row_index, col_index, value)
    return {"found": True, "updated": True}


@function_tool
def mark_post_published_tool(title_reference: str) -> dict:
    """Marks a post's Published column as "Yes" in generated_posts, matched
    by a case-insensitive partial title match. Use this when the user tells
    you a post is already published (e.g. they published it manually, or it
    went out some other way) and the sheet needs to reflect that reality --
    otherwise the pipeline's own `post` stage would try to publish it again
    (Sanity has no dedup, so that would create a duplicate)."""
    return _update_generated_posts_column(title_reference, PUBLISHED_COLUMN, "Yes")


@function_tool
def set_post_approval_tool(title_reference: str, approved: bool) -> dict:
    """Sets a post's Approve/Disapprove column in generated_posts, matched
    by a case-insensitive partial title match. Use this when the user tells
    you in chat to approve or reject a specific draft by name, as an
    alternative to reacting ✅/❌ in the approval channel."""
    status = "Approved" if approved else "Rejected"
    return _update_generated_posts_column(title_reference, APPROVE_DISAPPROVE_COLUMN, status)


# Must match scripts/run_stage.py's _CREATED_AT_FORMAT exactly -- that's the
# side that actually writes the "Created At" column.
_CREATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S UTC"


def _resolve_latest_post_title() -> Optional[str]:
    """Falls back to the most recent post still awaiting review
    (Approve/Disapprove empty), or if none are pending, the most recently
    generated post overall. Covers the common case where the user is
    reacting to the draft that was JUST posted for review and just says
    "shorten the intro" without naming it.

    Prefers the "Created At" column (written deterministically by
    scripts/run_stage.py) for an unambiguous answer to "which is latest".
    Falls back to sheet row order if that column is missing/unparseable on
    every candidate row -- still a reliable recency proxy since rows are
    only ever appended, but explicit timestamps are worth trusting over
    positional inference whenever they're actually there."""
    try:
        records = _get_worksheet().get_all_records()
    except Exception as e:
        logger.warning(f"_resolve_latest_post_title: failed to read generated_posts: {e}")
        return None
    if not records:
        return None
    pending_review = [r for r in records if not str(r.get("Approve/Disapprove", "")).strip()]
    candidates = pending_review if pending_review else records

    timestamped = []
    for row in candidates:
        raw = str(row.get("Created At", "")).strip()
        if not raw:
            continue
        try:
            timestamped.append((datetime.strptime(raw, _CREATED_AT_FORMAT), row))
        except ValueError:
            continue

    target = max(timestamped, key=lambda pair: pair[0])[1] if timestamped else candidates[-1]
    title = str(target.get("Title", "")).strip()
    return title or None


def _request_post_edit(edit_instruction: str, title_reference: Optional[str] = None) -> dict:
    """Shared by edit_post_content_tool (chat) and the /edit slash command --
    both need the exact same title-resolution + dispatch behavior, so it
    lives in one place rather than being duplicated. See
    edit_post_content_tool's docstring for the full behavior description."""
    resolved_title = (title_reference or "").strip()
    if not resolved_title:
        resolved_title = _resolve_latest_post_title() or ""
        if not resolved_title:
            return {"status": "error", "error": "No title given, and there's no post in generated_posts to default to."}
    try:
        already_running = dispatch_workflow(
            "edit_post",
            extra_inputs={"edit_title": resolved_title, "edit_instruction": edit_instruction},
        )
    except Exception as e:
        logger.exception("_request_post_edit: failed to dispatch edit_post")
        return {"status": "error", "error": str(e)}
    return {
        "status": "triggered",
        "title": resolved_title,
        "edit_instruction": edit_instruction,
        "queued_behind_another_run": already_running,
    }


@function_tool
def edit_post_content_tool(edit_instruction: str, title_reference: Optional[str] = None) -> dict:
    """Requests a targeted content edit to an EXISTING post, instead of full
    approve/reject or a full regeneration. Use this when the user wants a
    SPECIFIC change made to a post's content -- e.g. "shorten the intro on
    X", "fix the claim about Y in the pricing post", "remove the third
    bullet point" -- as opposed to approving/rejecting/publishing it
    wholesale, which the other tools already handle.

    title_reference is matched case-insensitively as a partial title. LEAVE
    IT EMPTY if the user doesn't name a specific post -- most edit requests
    come right after a draft was posted for review, so they'll often just
    say "shorten the intro" with no title at all. Omitting title_reference
    resolves to the most recent post still awaiting review (or, if nothing
    is pending review, the most recently generated post overall). The
    result's "title" field tells you which post it actually resolved to --
    mention that in your reply so the user can correct you if it guessed
    wrong.

    Does NOT do the edit itself -- it dispatches the pipeline's edit_post
    stage via GitHub Actions, which runs the actual targeted-edit agent
    (constrained to change only what was asked and preserve everything else:
    structure, headings, links, FAQs, tone, and length) and saves the result.
    If the post is already published, that stage also patches the live
    Sanity document so the site reflects the edit, not just the sheet. The
    result includes queued_behind_another_run: if true, tell the user the
    edit will run once the currently-active pipeline run finishes."""
    return _request_post_edit(edit_instruction, title_reference)


class _ChatAgentHooks(AgentHooks):
    """Without this, the ContentSpark Assistant's tool calls (deciding to
    check status, prioritize a topic, trigger a stage, request an edit) are
    completely invisible in the logs -- confirmed live in this exact repo
    twice already for the main pipeline's agents (image_agent.py,
    posting_agent.py both shipped with no hooks at all). logger.info instead
    of print() since this runs as a long-lived process, not a one-shot
    script -- consistent with the rest of this file's logging."""

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        logger.info(f"[chat] Agent start: {agent.name}")

    async def on_agent_end(self, context: RunContextWrapper, agent: Agent, result) -> None:
        logger.info(f"[chat] Agent end: {agent.name}")

    async def on_tool_start(self, context: RunContextWrapper, agent: Agent, tool: Tool) -> None:
        logger.info(f"[chat] Tool start: {tool.name}")

    async def on_tool_end(self, context: RunContextWrapper, agent: Agent, tool: Tool, result) -> None:
        logger.info(f"[chat] Tool end: {tool.name} -> {result}")


def _build_discord_agent() -> Optional[Agent]:
    if not OPENROUTER_API_KEY:
        return None
    client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    model = OpenAIChatCompletionsModel(model=DEEPSEEK_MODEL, openai_client=client)
    return Agent(
        name="ContentSpark Assistant",
        instructions=(
            "You are the ContentSpark AI pipeline's Discord assistant. You help the "
            "site owner keep track of their SEO blog pipeline (research -> brief -> "
            "content -> approval -> publish), can discuss topic ideas with them, and "
            "CAN take action yourself instead of just describing what to do. Be "
            "concise and conversational, like a helpful colleague, not a formal "
            "report.\n\n"
            "- Call get_pipeline_status_tool whenever a question is about counts, "
            "what's queued, or what's pending -- don't guess numbers.\n"
            "- When the user names a specific topic and asks to prioritize it, run "
            "it now, or write/generate content for it: call prioritize_topic_tool "
            "with that topic first (it moves the matching row to the top of "
            "whichever queue it's in, without deleting anything), then call "
            "trigger_stage_tool with the next_stage it returns to actually kick off "
            "that run.\n"
            "- If prioritize_topic_tool reports found=false, say so plainly and ask "
            "for the exact title rather than guessing.\n"
            "- When the user tells you a post is already published (manually, or "
            "some other way outside the normal flow), call mark_post_published_tool "
            "so the pipeline doesn't try to publish it again and create a "
            "duplicate -- Sanity has no dedup.\n"
            "- When the user tells you to approve or reject a specific draft by "
            "name, call set_post_approval_tool instead of telling them to react in "
            "the approval channel -- you can do it directly.\n"
            "- When the user wants a SPECIFIC change made to a specific existing "
            "post's content (fix a claim, shorten a section, reword something, "
            "remove a point) -- as opposed to approving/rejecting/publishing it "
            "wholesale, or wanting it regenerated from scratch -- call "
            "edit_post_content_tool with exactly what to change. If they don't name "
            "which post (very common -- they're usually reacting to whatever draft "
            "was just posted for review), leave title_reference empty; it defaults "
            "to the latest post awaiting review. Always mention which post it "
            "resolved to (the result's \"title\" field) so they can correct you if "
            "it guessed wrong. It preserves the post's structure, links, FAQs, and "
            "SEO fields and only changes what was asked; if the post is already "
            "live it updates the published version too.\n"
            "- Any tool that can return queued_behind_another_run=true "
            "(trigger_stage_tool, edit_post_content_tool) means a pipeline run was "
            "already active when you triggered this one -- say clearly that it's "
            "queued and will start once the current run finishes, don't imply it's "
            "running right now.\n"
            "- Do this yourself -- don't just tell the user to run a slash command "
            "or react to a message when you can call these tools directly instead."
        ),
        tools=[
            get_pipeline_status_tool,
            prioritize_topic_tool,
            trigger_stage_tool,
            mark_post_published_tool,
            set_post_approval_tool,
            edit_post_content_tool,
        ],
        hooks=_ChatAgentHooks(),
        model=model,
    )


# Built once at import time -- OPENROUTER_API_KEY doesn't change at runtime,
# so there's no reason to rebuild the client/agent on every message.
_discord_agent = _build_discord_agent()


async def ask_discord_agent(user_message: str) -> str:
    """Runs the ContentSpark Assistant agent on a user's message. Stateless
    per call -- no conversation history/session is kept, so each mention is
    answered fresh (the agent calls get_pipeline_status_tool itself if the
    question needs live counts, rather than every message paying for a
    sheet read whether it needs one or not)."""
    if _discord_agent is None:
        return "Chat isn't configured yet -- ask the admin to set OPENROUTER_API_KEY."
    logger.info(f"[chat] Received: {user_message!r}")
    try:
        result = await Runner.run(_discord_agent, user_message, max_turns=6)
        reply = str(result.final_output)
        logger.info(f"[chat] Replied ({len(reply)} chars).")
        return reply
    except Exception as e:
        logger.exception("Discord agent run failed")
        return f"⚠️ Couldn't get a response: {e}"


def _github_actions_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _has_in_progress_or_queued_run() -> bool:
    """Checks whether pipeline.yml already has a run in_progress or queued.
    pipeline.yml now has a concurrency group with cancel-in-progress: false
    (added to stop overlapping runs from racing on the same sheet row), so a
    new manual trigger while another run is active won't start immediately
    -- it queues behind it. Used to tell the user that plainly instead of
    implying their trigger started right away."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/pipeline.yml/runs"
    try:
        for status in ("in_progress", "queued"):
            response = requests.get(
                url, headers=_github_actions_headers(), params={"status": status, "per_page": 1}, timeout=15
            )
            response.raise_for_status()
            if response.json().get("total_count", 0) > 0:
                return True
    except Exception as e:
        logger.warning(f"_has_in_progress_or_queued_run: check failed, assuming none running: {e}")
    return False


def dispatch_workflow(stage: str, extra_inputs: Optional[dict] = None) -> bool:
    """Dispatches pipeline.yml via workflow_dispatch. Returns True if another
    run was already in_progress/queued at dispatch time -- meaning this new
    run will queue behind it rather than start immediately -- so callers can
    surface that to the user."""
    already_running = _has_in_progress_or_queued_run()
    inputs = {"stage": stage}
    if extra_inputs:
        inputs.update(extra_inputs)
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/pipeline.yml/dispatches"
    response = requests.post(
        url, headers=_github_actions_headers(), json={"ref": "master", "inputs": inputs}, timeout=15
    )
    response.raise_for_status()
    return already_running


def _extract_title(message_content: str):
    for line in message_content.splitlines():
        if line.startswith("**Title:**"):
            return line.removeprefix("**Title:**").strip()
    return None


def _extract_candidate_topic(message_content: str):
    for line in message_content.splitlines():
        if line.startswith("**Candidate Topic:**"):
            return line.removeprefix("**Candidate Topic:**").strip()
    return None


intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True


class ContentSparkBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.last_event_at = time.monotonic()

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Slash commands synced.")
        self.connection_watchdog.start()
        self.weekly_digest.start()

    async def on_socket_event_type(self, event_type: str) -> None:
        # Fires for every gateway event, including heartbeats -- the
        # cheapest possible "are we actually still receiving data" signal.
        self.last_event_at = time.monotonic()

    async def on_disconnect(self) -> None:
        logger.warning("Gateway disconnected; discord.py will attempt to reconnect.")

    @tasks.loop(seconds=WATCHDOG_INTERVAL_SECONDS)
    async def connection_watchdog(self) -> None:
        idle_for = time.monotonic() - self.last_event_at
        if idle_for < STALE_CONNECTION_SECONDS:
            return
        logger.critical(
            "health-monitor: restarting (reason: stale-socket, idle_for=%.0fs)", idle_for
        )
        await self.close()
        # discord.py's own reconnect loop assumes the socket knows it's
        # broken. A "stale" connection is one it doesn't know is broken, so
        # this matches OpenClaw's actual fix -- restart the whole process --
        # rather than trying to force a resume from application code.
        #
        # sys.exit() from inside an asyncio Task doesn't reliably terminate
        # the process (asyncio just logs it as an unhandled task exception);
        # os._exit() forces immediate process termination so Dokploy's
        # restart-always policy actually restarts the container. Cleanup
        # already happened via close() above, so skipping atexit/finally
        # blocks here is fine.
        os._exit(1)

    @connection_watchdog.before_loop
    async def _before_watchdog(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(hours=WEEKLY_DIGEST_INTERVAL_HOURS)
    async def weekly_digest(self) -> None:
        if self.weekly_digest.current_loop == 0:
            # tasks.loop fires immediately on start(), so without this every
            # bot restart/redeploy (Dokploy deploys, the connection watchdog
            # restarting on a stale socket) would post an out-of-cycle
            # digest instead of waiting a full 7 days like the name implies.
            return
        channel = self.get_channel(APPROVAL_CHANNEL_ID) or await self.fetch_channel(APPROVAL_CHANNEL_ID)
        try:
            digest = await _build_weekly_digest(channel)
        except Exception as e:
            logger.exception("Failed to build weekly digest")
            digest = f"⚠️ Failed to build the weekly digest: {e}"
        logger.info("[digest] Posting weekly digest.")
        await channel.send(digest)

    @weekly_digest.before_loop
    async def _before_digest(self) -> None:
        await self.wait_until_ready()


bot = ContentSparkBot()


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (id={bot.user.id})")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if bot.user is not None and payload.user_id == bot.user.id:
        return
    if payload.channel_id != APPROVAL_CHANNEL_ID:
        return
    emoji = str(payload.emoji)
    if emoji not in ("✅", "❌"):
        return

    channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    logger.info(f"[reaction] {emoji} on message {payload.message_id} in approval channel")

    candidate_topic = _extract_candidate_topic(message.content)
    if candidate_topic:
        if emoji == "❌":
            logger.info(f"[reaction] Skipped topic candidate: {candidate_topic!r}")
            await channel.send(f"❌ Skipped topic candidate: **{candidate_topic}**")
            return
        duplicate_match = await _check_duplicate_topic(candidate_topic)
        try:
            add_keyword(candidate_topic)
        except Exception as e:
            logger.exception("Failed to add topic candidate %r", candidate_topic)
            await channel.send(f"⚠️ Failed to add **{candidate_topic}** to the research queue: {e}")
            return
        logger.info(f"[reaction] Added topic candidate to research queue: {candidate_topic!r} (duplicate_match={duplicate_match!r})")
        reply = f"✅ Added **{candidate_topic}** to the research queue."
        if duplicate_match:
            reply += f"\n⚠️ This looks similar to an existing topic: **{duplicate_match}** -- added anyway, but you may want to check before it gets researched."
        await channel.send(reply)
        return

    title = _extract_title(message.content)
    if not title:
        return

    status = "Approved" if emoji == "✅" else "Rejected"
    try:
        found = set_approval(title, status)
    except Exception as e:
        logger.exception("Failed to update sheet for title %r", title)
        await channel.send(f"⚠️ Failed to record {status.lower()} for **{title}**: {e}")
        return

    logger.info(f"[reaction] Title {title!r} -> {status} (found={found})")
    if found:
        await channel.send(f"{'✅' if status == 'Approved' else '❌'} **{title}** marked {status.lower()}.")
    else:
        await channel.send(f"⚠️ Couldn't find a row matching **{title}** in `generated_posts`.")


@bot.tree.command(name="run", description="Trigger a ContentSpark pipeline stage")
@app_commands.describe(stage="Which stage to run")
@app_commands.choices(stage=[app_commands.Choice(name=s, value=s) for s in STAGE_CHOICES])
async def run_stage_command(interaction: discord.Interaction, stage: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)
    logger.info(f"[/run] Requested by {interaction.user}: stage={stage.value}")
    try:
        already_running = dispatch_workflow(stage.value)
    except Exception as e:
        logger.exception("Failed to dispatch workflow for stage %s", stage.value)
        await interaction.followup.send(f"⚠️ Failed to trigger `{stage.value}`: {e}")
        return
    logger.info(f"[/run] Dispatched stage={stage.value} (queued_behind_another_run={already_running})")
    if already_running:
        await interaction.followup.send(
            f"🚀 Triggered `{stage.value}` -- another run is already in progress, "
            "so this one is queued and will start once it finishes."
        )
    else:
        await interaction.followup.send(f"🚀 Triggered `{stage.value}`. Check the Actions tab for progress.")


@bot.tree.command(name="add_topic", description="Add a keyword, concept, problem, or transcript to the research queue")
@app_commands.describe(
    text="A short concept or problem statement (leave empty if attaching a file)",
    file="A .txt file for longer content like a full transcript (leave empty if using text)",
)
async def add_topic_command(
    interaction: discord.Interaction,
    text: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    await interaction.response.defer(thinking=True)

    if not text and not file:
        await interaction.followup.send("⚠️ Provide either `text` or a `file` attachment.")
        return

    parts = []
    if text:
        parts.append(text.strip())
    if file:
        if not file.filename.lower().endswith(".txt"):
            await interaction.followup.send("⚠️ Only `.txt` file attachments are supported.")
            return
        try:
            raw = await file.read()
            parts.append(raw.decode("utf-8").strip())
        except UnicodeDecodeError:
            await interaction.followup.send("⚠️ Couldn't decode that file as UTF-8 text.")
            return
        except Exception as e:
            logger.exception("Failed to read attachment %s", file.filename)
            await interaction.followup.send(f"⚠️ Failed to read the attached file: {e}")
            return

    content = "\n\n".join(p for p in parts if p)
    if not content:
        await interaction.followup.send("⚠️ That submission was empty after trimming whitespace.")
        return

    truncated = len(content) > MAX_KEYWORD_CELL_LENGTH
    if truncated:
        content = content[:MAX_KEYWORD_CELL_LENGTH]

    # Only worth checking for short topic/keyword-style submissions -- a
    # full transcript isn't a "topic" to dedupe against titles, and running
    # the check against tens of thousands of characters would be wasteful.
    duplicate_match = await _check_duplicate_topic(content) if len(content) <= 300 else None

    try:
        add_keyword(content)
    except Exception as e:
        logger.exception("Failed to add topic to ContentSpark_Keywords")
        await interaction.followup.send(f"⚠️ Failed to add to the research queue: {e}")
        return

    preview = content[:150] + ("…" if len(content) > 150 else "")
    reply = f"✅ Added to the research queue:\n> {preview}"
    if duplicate_match:
        reply += f"\n⚠️ This looks similar to an existing topic: **{duplicate_match}** -- added anyway, but you may want to check before it gets researched."
    if truncated:
        reply += "\n⚠️ Content was truncated to fit Google Sheets' 50,000-character cell limit."
    logger.info(f"[/add_topic] Requested by {interaction.user}: added {len(content)} chars to ContentSpark_Keywords")
    await interaction.followup.send(reply)


@bot.tree.command(name="status", description="Show how many keywords/briefs/posts are queued at each pipeline stage")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    logger.info(f"[/status] Requested by {interaction.user}")
    try:
        status = gather_pipeline_status()
    except Exception as e:
        logger.exception("Failed to gather pipeline status")
        await interaction.followup.send(f"⚠️ Failed to read pipeline status: {e}")
        return
    await interaction.followup.send(format_status_report(status))


@bot.tree.command(name="edit", description="Request a specific content edit to an existing post")
@app_commands.describe(
    instruction="The specific change to make (e.g. \"shorten the intro\", \"fix the claim about X\")",
    title="Post title, or partial title (leave empty to target the latest post awaiting review)",
)
async def edit_command(interaction: discord.Interaction, instruction: str, title: Optional[str] = None):
    await interaction.response.defer(thinking=True)
    logger.info(f"[/edit] Requested by {interaction.user}: title={title!r} instruction={instruction!r}")
    result = _request_post_edit(instruction, title)
    if result.get("status") == "error":
        logger.warning(f"[/edit] Failed: {result.get('error')}")
        await interaction.followup.send(f"⚠️ {result.get('error')}")
        return
    logger.info(f"[/edit] Dispatched edit_post for {result['title']!r} (queued_behind_another_run={result.get('queued_behind_another_run')})")
    reply = f"✏️ Requested edit to **{result['title']}**: {instruction}"
    if result.get("queued_behind_another_run"):
        reply += "\n(Another run is already in progress -- this will start once it finishes.)"
    await interaction.followup.send(reply)


async def _send_chunked(channel, text: str, limit: int = 1900) -> None:
    while text:
        await channel.send(text[:limit])
        text = text[limit:]


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user is None or bot.user not in message.mentions:
        return

    question = message.content
    for mention in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
        question = question.replace(mention, "")
    question = question.strip()
    if not question:
        question = "What's the current pipeline status? Give me a quick overview."

    async with message.channel.typing():
        reply = await ask_discord_agent(question)
    await _send_chunked(message.channel, reply)

    # This bot only uses slash commands (app_commands), but calling this is
    # the documented discord.py pattern for any bot that overrides on_message
    # -- cheap insurance against silently breaking prefix commands later.
    await bot.process_commands(message)


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
