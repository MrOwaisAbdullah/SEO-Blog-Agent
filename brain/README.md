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

**Two kinds of file live here now** (`get_brain_notes_tool` reads both, but
`content_generator_agent` treats them very differently — see its Step 2.5 instructions):
- **Real voice entries** — anything you write by hand. First-person, gets woven into drafts as
  genuine experience.
- **`coverage-*.md`** — auto-generated, see "Auto-logged coverage history" below. Never
  first-person, never treated as an opinion; used only for topic awareness.

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
distills, the system never auto-writes a real entry.** A `mine_feedback` stage
(`scripts/run_stage.py::run_mine_feedback`) scans `review_feedback_log` for a recurring
pattern — "posts about X keep getting rejected," "Y-style titles always get approved" — and
posts candidate brain entries to Discord for review. It runs automatically right after every
approval (`discord_bot/bot.py`'s `_log_review_feedback` dispatches it — see the code comment
there for why the trigger moved but the review gate didn't), but the gate itself is unchanged:
it still needs `_MINE_FEEDBACK_MIN_ROWS` real rows before it attempts anything, and it still
only *posts a proposal* — nothing gets written into this folder from that path automatically;
the owner authors each real entry by hand, same as ever.

## Auto-logged coverage history (`coverage-*.md`)

Different from the above, and the one thing in this folder that IS written automatically:
`scripts/run_stage.py::run_log_coverage` runs daily, reads `review_feedback_log` for
newly-**approved** (never rejected) posts, and writes one small `coverage-<slug>.md` file per
post — title, tags derived from the title, approval date, quality score, summary. It then
commits and pushes those files itself (this is the one stage in the whole pipeline that writes
back to its own repo). Explicitly a **factual record, not an opinion**: every file states
plainly that it's an auto-logged coverage record, not personal voice, and
`content_generator_agent` is instructed to use it only for topic awareness (don't repeat an
angle already covered) — never to quote it as a first-person story. This is why it's safe to
auto-write even though the rest of this folder isn't: nothing here is invented, it's just a
fact ("this topic was covered and approved") with no fabricated reasoning attached.
