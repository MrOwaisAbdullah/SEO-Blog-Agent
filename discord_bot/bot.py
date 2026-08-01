"""
Discord bot for ContentSpark AI.

Two responsibilities:
  1. Approvals -- listens for checkmark/cross reactions on draft-preview
     messages (posted by scripts/run_stage.py's Discord webhook call after the
     "content" stage) and writes the result into the Approve/Disapprove column
     of the generated_posts worksheet.
  2. Triggering -- a /run slash command that dispatches pipeline.yml via
     GitHub's workflow_dispatch API, for on-demand runs.

Deliberately self-contained: talks to Google Sheets directly via gspread
instead of importing tools/sheet_tool.py from the main pipeline. That module
imports the openai-agents SDK at the top level (for its @function_tool
decorators), which this container has no other reason to need, and importing
it would couple this bot's deploy to changes in the pipeline's tools/ code
even though they deploy independently (see .github/workflows/deploy-bot.yml's
path filter).
"""
import json
import logging
import os
import time

import discord
import gspread
import requests
from discord import app_commands
from discord.ext import commands, tasks
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("contentspark-bot")

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GITHUB_PAT = os.environ["GITHUB_PAT"]
GITHUB_REPO = os.environ["GITHUB_REPO"]  # "owner/repo"
APPROVAL_CHANNEL_ID = int(os.environ["DISCORD_APPROVAL_CHANNEL_ID"])

SHEET_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
SPREADSHEET_NAME = "ContentSpark"
WORKSHEET_NAME = "generated_posts"
# Title, Generated Content, FAQs, Quality Score, Summary, Approve/Disapprove, Published
APPROVE_DISAPPROVE_COLUMN = 6

STAGE_CHOICES = ["research", "brief", "content", "post"]

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


def _get_worksheet():
    """Cached, same rationale as tools/sheet_tool.py's client caching:
    google-auth Credentials refresh their own tokens, so it's safe to reuse
    one authorized client across events instead of re-authenticating on every
    reaction/command."""
    global _gspread_client
    if _gspread_client is None:
        creds_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
        creds = Credentials.from_service_account_info(creds_info, scopes=SHEET_SCOPE)
        _gspread_client = gspread.authorize(creds)
    return _gspread_client.open(SPREADSHEET_NAME).worksheet(WORKSHEET_NAME)


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


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
