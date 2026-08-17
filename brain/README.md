# Brain

This folder is different from `get_author_context_tool` (in `tools/tools.py`), which is a
**static style guide** — tone, banned words, personas, CTAs. That controls how the writing
*sounds*. It has nothing to say about what the writer actually knows.

This folder is **what the writer actually knows**: real projects shipped, real numbers, real
opinions formed from doing the work, real mistakes. It's the difference between a post that
sounds confident and a post that IS the account of someone who did the thing. Fed into a
draft, one real number or one real opinion does more for trust than another paragraph of
competent-sounding generalities ever will.

It starts empty on purpose. **Never invent an entry on the owner's behalf** — a fabricated
"I saved $X by doing Y" is a worse failure than having no brain note at all, because it reads
as a genuine claim readers can be misled by. Entries only go in here when the owner actually
provides them.

## Format

One entry per file, `.md`, any filename except `README.md` and anything starting with `_`.
First line is a `# Title`. Somewhere in the first few lines, a `Tags: tag1, tag2, tag3` line —
these are what `get_brain_notes_tool` matches against the topic/keyword of the piece being
written. Everything after that is free-form: the story, the number, the opinion, in the
owner's own words. See `_TEMPLATE.md`.

## How it's used

`content_generator_agent` calls `get_brain_notes_tool(topic)` after loading the style guide
and before drafting. If it finds a tag match, it's told to weave the real example in — not to
paraphrase it into something generic, and not to add details beyond what the entry actually
says. If nothing matches, the agent proceeds without one and does not fabricate a personal
anecdote to fill the gap.

## Growing this

There's no automated write-back into THIS folder — unlike the style guide, which barely
changes, real entries are meant to accumulate over time as the owner has more to say. Add a new
file whenever there's a real story, real number, or real opinion worth writing in. Ten good
entries covering the site's core recurring topics is enough to start noticeably changing how
posts read.

## The review-feedback loop (`review_feedback_log` Google Sheet, not a local file)

Every ✅/❌ approve/reject decision in the Discord approval channel (and every
`set_post_approval_tool` call from chat) gets appended to a `review_feedback_log` worksheet in
the same spreadsheet as everything else — timestamp, status, title, score, summary. **Not a
local file under `brain/`**: the Discord bot's Docker build context is `discord_bot/` only (see
`discord_bot/Dockerfile`'s single `COPY bot.py`), so it has no filesystem access to this
directory at all, and its container doesn't persist across redeploys anyway. Google Sheets is
the one storage this bot and the GitHub-Actions-executed content pipeline both actually reach —
same reason every other piece of pipeline state lives there instead of in a file.

This is a **raw log, not a brain entry**: a bare Approved/Rejected has no reasoning attached, so
`get_brain_notes_tool` never reads it — it only ever looks at `brain/*.md`.

The point of keeping it is self-learning by the same rule as everything else here: **the owner
distills, the system never auto-writes.** A `mine_feedback` stage (on-demand, see
`scripts/run_stage.py`) periodically scans `review_feedback_log` for a recurring pattern —
"posts about X keep getting rejected," "Y-style titles always get approved" — and posts
candidate brain entries to Discord for review. Nothing gets written into this folder
automatically; the owner approves each one, entry by entry, same as every other write-back gate
in this system.
