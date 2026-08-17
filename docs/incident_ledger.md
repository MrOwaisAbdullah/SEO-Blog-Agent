# Incident Ledger

A running record of settled bugs and closed decisions in this pipeline, kept so nobody re-pays
the cost of re-discovering the same failure class from scratch. **Check here before
investigating anything that "smells familiar,"** and before proposing a fix to retry/fallback
logic, Sheets writes, or Sanity publishing specifically — those three areas have the deepest
history of the same root cause recurring in a new spot.

Format per entry: Symptom, Root cause, Evidence (commit hash — read the actual diff before
reusing a fix), Status. Status vocabulary: **FIXED** (gone, in mainline, regression if it
reappears), **FENCED** (a guard now prevents the mistake, even though the temptation remains),
**OPEN** (known, not fully closed), **SETTLED** (a deliberate call — don't re-propose without
new evidence).

## Index by subsystem

- **Agent retry/fallback** (`blog_agent/custom_runner.py`, `research_agent.py`): E1–E3 — worst
  status FIXED (E3's class-level guard now covers every known call site, see below; E2's
  "write inside an agent's own retryable loop" bug class is now fixed for both research and
  content).
- **Sheets I/O** (`tools/sheet_tool.py`, `discord_bot/bot.py`): E4 — FIXED
- **Sanity publishing** (`tools/tools.py`, `posting_agent.py`): E5–E7 — FIXED
- **Internal links** (`tools/tools.py`, `blog_agents.py` prompts): E8–E9 — FIXED
- **Discord scheduling / repurpose** (`discord_bot/bot.py`, `scripts/run_stage.py`): E10–E12 —
  FIXED
- **Security / secrets**: E13–E14 — E13 FENCED, E14 **OPEN** (needs the owner, not code)

---

## E1 — Selected keyword permanently lost on model retry

**Symptom:** A prioritized topic (GLM 5.3) vanished from the queue and the research stage
crashed, with no research produced.

**Root cause:** `get_keyword_tool` marked a row "used" the instant it was selected, with no
way back. When the model call itself failed (503) and the fallback runner re-ran the whole
Triage Agent from scratch, the retry found the row already burned and reported "no keywords
available" — the topic was gone with zero research done.

**Evidence:** `33039a0` — added an in-process claim (row_index + keyword) that re-issues the
same row on a same-process retry instead of re-scanning, plus `release_keyword_claim()` /
`clear_keyword_claim()` so a genuine downstream failure returns the row to "available" instead
of burning it.

**Status:** FIXED.

## E2 — Duplicate research_data row from a replayed write

**Symptom:** The GLM-5.3 research run wrote two rows for the same keyword; a human had to
manually delete the duplicate.

**Root cause:** The Output Agent's `append_row` tool call succeeded, then the agent's *next*
model turn hit a 429. `run_with_fallback` restarted the whole agent from scratch on a different
model, which called `append_row` again for the same finding — a write inside an agent's own
tool-calling loop is not safe to replay on a fallback restart.

**Evidence:** `ed32e04` — moved the actual sheet write out of the agent's tool-calling loop
entirely. The Output Agent now only returns JSON; `combined_research_workflow` parses it and
calls `manage_sheet_data(append_row)` itself, in plain Python, exactly once, after the retry
loop has already exited.

**Status:** FIXED for the research stage, and now also FIXED for the content stage.
`content_generator_agent` (`blog_agent/blog_agents.py`) previously had this exact same shape —
three separate `manage_sheet_data_tool` writes (`generated_posts`, `claims_audit`,
`content_briefs`) inside its own agent turn. Migrated to the same plain-Python-after-the-loop
pattern: Steps 6/6.5/7 no longer call `manage_sheet_data_tool` to save anything — the agent
returns `Title`/`Generated Content`/`FAQs`/`Quality Score`/`Summary`/`Claims Notes` as JSON only,
and `scripts/run_stage.py::run_content()` does all three writes itself after
`run_with_fallback` fully returns: `_ensure_content_persisted()` (already existed, already
title-deduped — it was the salvage-path fallback, now it's the only path) for `generated_posts`,
the new `_persist_claims_audit()` for `claims_audit` (skips silently if `Claims Notes` is empty
rather than fabricate one), and the already-existing `_graduate_content_brief()` for
`content_briefs` cleanup (it already ran unconditionally after every successful save, which made
the agent's own old Step 7 write redundant even before this fix). Verified with a fake in-memory
sheet: single write on success, no duplicate row on a simulated replay, no claims row written
when Claims Notes is empty. **General rule going forward:** any write that must happen exactly
once should not be a tool inside an agent's own retryable loop — do the write in the
orchestrating Python code after the agent returns, not as a tool call the agent can be made to
repeat.

## E3 — False-positive failure detection ("error" substring in a successful result) — recurring class, not one bug

**Symptom:** A fully successful agent run gets treated as failed and retried/replayed, because
its own legitimate output happened to contain the word "error" in prose (e.g. discussing "a
configuration error" as a finding) — or conversely, a real failure with paraphrased wording
slips through as a false success.

**Root cause:** The pattern `"error" not in str(result).lower()` (or similar substring checks)
treats the whole string repr of a `RunResult` as a status signal, when it's actually the full
model output. This is unreliable in both directions.

**Evidence of the pattern recurring across independent incidents, each fixed locally:**
`0ef9fc1`, `50559a5`, `a132186`, `420ebe7`, `08edd68`, `edbebcb`, `33039a0` (this last one added
`_run_looks_failed()` in `research_agent.py`, which only trusts an explicit `{"status":
"error"}` / bare `{"error": ...}` JSON envelope, never a substring match on the repr).

**Status: FIXED as a class.** The checker moved to `lib/run_result_utils.py` as
`run_looks_failed()` (research_agent.py now imports it rather than defining its own copy) and
every remaining raw `"error" not in/in str(result).lower()` call site was migrated to it:
`image_agent.py` (image selection + contextual insertion retry loops, 2 sites) and
`posting_agent.py` (the Posting Agent retry loop, and the post-loop failure gate — 2 sites, both
guarded first by `_sanity_publish_already_succeeded()`, which is a stronger tool-output-based
signal specific to the Sanity publish step and stays as the primary check there).
`_prep_or_contextual_succeeded()` in `posting_agent.py` was already its own precise
positive-marker check (POST_DATA block presence) predating this fix and did not need migrating.
Verified: `run_looks_failed()` returns `False` for a successful run whose prose mentions "an
error" and `True` for a genuine `{"status": "error", ...}` envelope, both by direct test.
**If a new retry loop is added anywhere in this codebase, use `run_looks_failed()` from the
start — do not write a new raw substring check.**

## E4 — Sheets API rate limit silently lost approved topics

**Symptom:** Approving several topic candidates in quick succession (a burst of Discord
reactions) exhausted the Sheets API per-minute quota; every append after that point failed
immediately and the topic was gone (the reaction that triggered it was already consumed, so it
never re-fired).

**Root cause:** `add_keyword()` had no retry logic at all.

**Evidence:** `36d08e8` — added `_run_with_sheets_retry()`: retries `gspread.exceptions.APIError`
with exponential backoff (20s/40s/80s) using `asyncio.sleep` (not `time.sleep` — this runs
inside the bot's event loop, and a blocking sleep would freeze all other Discord processing for
the retry window).

**Status:** FIXED. **Rule:** any Sheets write reachable from a bot event loop needs
`asyncio`-safe backoff, not a blocking sleep.

## E5 — Infinite loop when a query legitimately finds zero results

**Symptom:** The content stage ran for 15+ minutes making the identical Sanity request
repeatedly with no delay, until manually cancelled.

**Root cause:** `fetch_internal_links`'s retry loop only broke on `if results: break`, and only
incremented `attempt` inside the `except` block. A successful request (200 OK) that legitimately
found zero matches — completely normal for a brand-new topic's category keywords — advanced
neither the break condition nor the retry counter, so `while attempt < max_retries` never
became false.

**Evidence:** `a271912` — now breaks after any successful request regardless of whether results
came back empty; only a genuine request failure consumes a retry attempt.

**Status:** FIXED. **Rule:** "the tool ran successfully and found nothing" is a different event
than "the tool call failed" — never let a retry counter conflate the two.

## E6 — Duplicate Sanity documents from overlapping/retried publishes

**Symptom:** The same post occasionally landed in Sanity twice (network retry, agent retry, or
two overlapping pipeline runs).

**Root cause:** Posts were created with `client.create()` and a random document ID — any repeat
publish attempt was a fresh document, not a no-op.

**Evidence:** `ccb35d4` — documents now get a deterministic slug-derived `_id` and use
`createIfNotExists`, so a repeat publish is a safe no-op. Also added a concurrency group to the
pipeline workflow so overlapping runs queue instead of racing.

**Status:** FENCED — the guard is structural (deterministic ID + `createIfNotExists`), so a
regression here would mean the guard itself broke, not that the old race resurfaced on its own.

## E7 — edit_post silently failed to patch the live site when titles diverge

**Symptom:** Three edit attempts in a row updated the Sheet but never the live Sanity document.

**Root cause:** `find_post_by_title` did an exact GROQ title match against the Sheet's Title
field, which can differ from what actually got published (the Preparation Agent can rephrase
the title at publish time).

**Evidence:** `1d3b431` — falls back to fuzzy-matching against every live post's title when the
exact match misses. Related: `5286e3c` also added a fallback path (`get_post_content_markdown`)
for posts with no `generated_posts` row at all (published before that column existed, or edited
directly in Studio).

**Status:** FIXED.

## E8 — Hallucinated internal links (e.g. `/blog/ai-tips`, a post that never existed)

**Symptom:** A published post linked to `/blog/ai-tips`, which has no matching post.

**Root cause:** The generator prompt's own worked examples used a fake internal link,
`[AI Tips](/blog/ai-tips)`, as filler. The model pattern-matched that literal example into real
generated posts instead of treating it as a placeholder.

**Evidence:** `5a49601` — replaced every example with the correct full-URL format
(`fetch_internal_links_tool` returns full `https://owaisabdullah.dev/blog/...` URLs, never
relative paths) and added an explicit rule: an internal link must come from an actual tool
result this run, or the sentence is written without one.

**Status:** FIXED. **Rule:** a prompt's illustrative examples are exactly as persuasive to the
model as real instructions — a fake-but-plausible example (a URL, a slug, a number) can and will
get copied into real output.

## E9 — fetch_internal_links infinite loop

See E5 — same tool, cross-referenced here because "internal links" and "the retry loop that
fetches them" are two different failure surfaces on the same function.

## E10 — Daily repurpose auto-spam contradicting an explicit "I decide" design

**Symptom:** The bot re-posted the full repurposing recommendation list to Discord every single
day regardless of whether anyone asked, confirmed across multiple days of near-identical
messages.

**Root cause:** `repurpose` was on a daily cron, and the cron path was the *only* way the
existing recommend-only mode was ever reached — the design explicitly wanted the owner to pick,
but the only wired trigger fired unprompted.

**Evidence:** `5286e3c` — removed the cron entry; repurpose is now only reachable on demand
(Discord chat tool or manual workflow dispatch).

**Status:** FIXED (this specific case). Treat as a reminder more broadly: adding a cron trigger
to a stage is a product decision (should this run unprompted?), not just a wiring detail —
verify against the actual intended design before scheduling one.

## E11 — Repurpose angle missing for 9 of 10 candidates, no error anywhere

**Symptom:** Only the first of 10 recommended posts got a suggested angle; the rest got
nothing, silently.

**Root cause:** The angle feature looked up each post's Summary from the `generated_posts`
sheet by exact title match. Most older posts have no `generated_posts` row at all (same gap
class as E7), so the lookup returned `""` and the code skipped the block with zero log output.

**Evidence:** `ba8c787` — `SanityAdapter.list_posts()` now also fetches `summary` directly (every
published post has one), used first, falling back to the sheet only if Sanity has nothing. Added
an explicit log line when a summary genuinely can't be found either way.

**Status:** FIXED. **Rule:** a silent `if summary:` skip with no `else` log line is how this
class of gap (title-keyed sheet lookup missing older/untracked posts) keeps resurfacing
invisibly — log the negative case, not just the positive one.

## E12 — timedelta NameError crashing freshness_sweep / search_performance_review

**Symptom:** A live "name 'timedelta' is not defined" failure.

**Root cause:** `scripts/run_stage.py` imported `datetime, timezone` but never `timedelta`,
despite both stages using it. `freshness_sweep` carried the identical latent bug — it just
hadn't been triggered yet.

**Evidence:** `689f806`.

**Status:** FIXED.

## E13 — Leaked Google service-account key in git history

**Symptom:** `contentspark-service-account-key.json` was blank in the working tree but the real
private key was present in two historical commits, reachable via `git show <sha>:<path>` on the
GitHub remote.

**Root cause:** The key was committed before the file was added to `.gitignore`.

**Evidence:** `docs/fixes_and_improvements.md` ("Critical: leaked Google service-account key") —
history was rewritten with `git filter-repo` and force-pushed; the file 404s on the current
tree. `.gitignore` now covers `credentials.json`, `google-credentials.json`,
`service-account-key.json`, `contentspark-service-account-key.json`, and `git ls-files` confirms
none are currently tracked.

**Status:** FENCED — the file can no longer be committed by accident (gitignored), and the old
exposure is scrubbed from reachable history.

## E14 — The leaked key itself was never confirmed rotated

**Symptom:** N/A — this is a follow-up, not a new incident.

**Root cause:** Scrubbing git history does not invalidate a key that was already exposed
publicly (even briefly, even in a private repo). The key documented in E13 must be rotated in
GCP IAM for the exposure to actually stop mattering.

**Evidence:** `docs/fixes_and_improvements.md`, "Still required (only you can do this): rotate
the key in GCP IAM."

**Status: OPEN.** This is not something a future coding session can close by editing files —
verify with the owner whether the key was rotated before treating this as resolved.
