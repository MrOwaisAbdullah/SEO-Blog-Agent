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
from typing import Optional

import discord
import gspread
import requests
from agents import Agent, AsyncOpenAI, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled
from discord import app_commands
from discord.ext import commands, tasks
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
    except Exception as e:
        logger.warning(f"gather_pipeline_status: failed to read generated_posts: {e}")
        status["posts_pending_review"] = None
        status["posts_approved_unpublished"] = None
        status["posts_total_generated"] = None

    try:
        published_records = _get_content_spark_worksheet("published_posts").get_all_records()
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
    if status.get("pending_review_samples"):
        lines.append("**Awaiting review:** " + ", ".join(status["pending_review_samples"]))
    return "\n".join(lines)


@function_tool
def get_pipeline_status_tool() -> dict:
    """Returns live counts of what's queued/pending at each ContentSpark
    pipeline stage: keywords queued, research pending a brief, briefs
    pending content, posts awaiting review, approved-but-unpublished posts,
    total generated posts, and total published posts -- plus a few sample
    titles for the queued/pending items. Call this whenever the question is
    about counts, what's queued, or what's currently pending; don't guess
    numbers."""
    return gather_pipeline_status()


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
            "content -> approval -> publish) and can discuss topic ideas with them. "
            "Be concise and conversational, like a helpful colleague, not a formal "
            "report. Call get_pipeline_status_tool whenever a question is about "
            "counts, what's queued, or what's pending -- don't guess numbers. You "
            "cannot take actions yourself (no publishing, no approving) -- if asked "
            "to do something, point to the right slash command (/run, /add_topic, "
            "/status) instead."
        ),
        tools=[get_pipeline_status_tool],
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
    try:
        result = await Runner.run(_discord_agent, user_message, max_turns=6)
        return str(result.final_output)
    except Exception as e:
        logger.exception("Discord agent run failed")
        return f"⚠️ Couldn't get a response: {e}"


def dispatch_workflow(stage: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/pipeline.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.post(
        url, headers=headers, json={"ref": "master", "inputs": {"stage": stage}}, timeout=15
    )
    response.raise_for_status()


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

    candidate_topic = _extract_candidate_topic(message.content)
    if candidate_topic:
        if emoji == "❌":
            await channel.send(f"❌ Skipped topic candidate: **{candidate_topic}**")
            return
        try:
            add_keyword(candidate_topic)
        except Exception as e:
            logger.exception("Failed to add topic candidate %r", candidate_topic)
            await channel.send(f"⚠️ Failed to add **{candidate_topic}** to the research queue: {e}")
            return
        await channel.send(f"✅ Added **{candidate_topic}** to the research queue.")
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

    if found:
        await channel.send(f"{'✅' if status == 'Approved' else '❌'} **{title}** marked {status.lower()}.")
    else:
        await channel.send(f"⚠️ Couldn't find a row matching **{title}** in `generated_posts`.")


@bot.tree.command(name="run", description="Trigger a ContentSpark pipeline stage")
@app_commands.describe(stage="Which stage to run")
@app_commands.choices(stage=[app_commands.Choice(name=s, value=s) for s in STAGE_CHOICES])
async def run_stage_command(interaction: discord.Interaction, stage: app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)
    try:
        dispatch_workflow(stage.value)
    except Exception as e:
        logger.exception("Failed to dispatch workflow for stage %s", stage.value)
        await interaction.followup.send(f"⚠️ Failed to trigger `{stage.value}`: {e}")
        return
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

    try:
        add_keyword(content)
    except Exception as e:
        logger.exception("Failed to add topic to ContentSpark_Keywords")
        await interaction.followup.send(f"⚠️ Failed to add to the research queue: {e}")
        return

    preview = content[:150] + ("…" if len(content) > 150 else "")
    reply = f"✅ Added to the research queue:\n> {preview}"
    if truncated:
        reply += "\n⚠️ Content was truncated to fit Google Sheets' 50,000-character cell limit."
    await interaction.followup.send(reply)


@bot.tree.command(name="status", description="Show how many keywords/briefs/posts are queued at each pipeline stage")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        status = gather_pipeline_status()
    except Exception as e:
        logger.exception("Failed to gather pipeline status")
        await interaction.followup.send(f"⚠️ Failed to read pipeline status: {e}")
        return
    await interaction.followup.send(format_status_report(status))


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
