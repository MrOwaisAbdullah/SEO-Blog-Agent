# Audit Findings & Fixes — 2026-08-01

This document records the issues found during a full audit of the ContentSpark AI
pipeline (LLM/Sheets/Sanity/Tavily/image integrations), the fix applied for each,
and how it was verified. Research for the modernization items used Context7
(library docs) and Tavily (web search); no live LLM/Sanity/Sheets calls were made
since no API keys were available in this environment — verification was limited to
static analysis, compile checks, and import/boot smoke tests via `TestClient`.

## Critical: leaked Google service-account key

**Found:** `contentspark-service-account-key.json` was blank in the working tree
but tracked in git history — commits `11ec1cb` and `5394015` contained the full,
real private key for service account project `amplified-bee-464906-r3`. The repo
(`github.com/MrOwaisAbdullah/SEO-Blog-Agent`, private) still had it reachable via
`git show <sha>:contentspark-service-account-key.json`.

**Fix:**
- Cloned a fresh mirror of `origin` into an isolated scratch directory (never
  touched the working copy or its uncommitted changes).
- Ran `git filter-repo --path contentspark-service-account-key.json --invert-paths`
  to strip the file from every commit.
- Verified the private key text no longer appears anywhere in the rewritten
  history, then force-pushed the rewritten `master` to `origin`.
- GitHub confirms the file now 404s on the current tree.

**Still required (only you can do this):** rotate the key in GCP IAM. Scrubbing
history does not invalidate a key that was already exposed — treat the old key as
compromised regardless of the history rewrite. Your local `master` and
`Extending-AgentRunner` branches still contain the old (unscrubbed) history until
you run `git fetch origin && git branch -f master origin/master`.

## Auth bypass

**File:** `main.py`

**Found:** `verify_api_key()` read `API_KEY` from the environment at module load,
then unconditionally overwrote it with the literal `"abc123"` inside the request
handler — a leftover dev value that made the real `API_KEY` setting irrelevant.
Any request with `Authorization: Bearer abc123` was accepted regardless of
production configuration.

**Fix:** removed the hardcoded overwrite; the handler now uses the module-level
`EXPECTED_API_KEY` and fails closed (500) if `API_KEY` isn't set, rather than
silently falling back to a known value.

**Verified:** booted the FastAPI app via `TestClient` and confirmed the app
constructs and serves without the removed variable.

## Dead handoff-chasing logic

**File:** `blog_agent/custom_runner.py`

**Found:** `FallbackAgentRunner.run_with_fallback` carried ~110 lines of
handoff-detection bookkeeping (`seen_agent_ids`, `processed_handoffs`,
`handoff_key` hashing, an outer iteration loop up to 10 passes) intended to chase
SDK-level agent handoffs across model-fallback attempts.

**Why it was dead:** per the OpenAI Agents SDK docs (Context7), `Runner.run()`
already resolves any `handoffs=[...]` configured on an agent within a single call
— it doesn't return early mid-handoff for a caller to continue manually. A
repo-wide `grep -rn "handoffs="` confirmed **no agent in this codebase configures
handoffs**; every multi-agent chain here (research → brief → content → posting) is
orchestrated manually in Python by calling `run_with_fallback()` once per agent
and passing the output forward as the next input. So the handoff branch could
never fire.

**Fix:** simplified `run_with_fallback` to a straightforward
retry-across-providers loop (same quota checks, performance sorting,
permanent/temporary error classification, and backoff as before), with the dead
branch removed. Also dropped the now-unused `hashlib` import.

**Verified:** module compiles and imports cleanly; full app boot smoke test
passes.

## Tracing sent to OpenAI despite not using OpenAI models

**File:** `main.py`

**Found:** `posting_agent.py` uses `trace("Posting Workflow")`, but nothing
disabled tracing export, and the README lists `OPENAI_API_KEY` as required "for
fallback LLMs" — yet every model in `custom_runner.LLM_MODELS` routes through
Gemini, OpenRouter, or Cohere, never OpenAI directly. Left as-is, prompt/content
data would be exported to OpenAI's trace backend for a service you don't
otherwise use for inference.

**Fix:** added `set_tracing_disabled(True)`, now run once at app startup (see
lifespan change below).

## Tavily tools blocking the event loop

**File:** `tools/search_tools.py`

**Found:** `tavily_search_tool`, `tavily_extract_tool`, `tavily_crawl_tool` used
the synchronous `TavilyClient` inside an async FastAPI/Agents SDK app.

**Fix:** switched to `AsyncTavilyClient` and made all three tools `async def`
with `await`. Confirmed via Context7 that Tavily's Python SDK ships an async
client (`tavily.AsyncTavilyClient`) mirroring the same `search`/`extract`/`crawl`
methods.

**Verified:** imported the tools and confirmed the SDK's `function_tool` wrapper
recognizes the underlying functions as coroutines (`asyncio.iscoroutinefunction`
on the wrapped function returns `True`).

## gspread re-authenticating on every call

**File:** `tools/sheet_tool.py`

**Found:** `manage_sheet_data_tool` created a brand-new gspread client and
re-resolved the spreadsheet **by title** (an extra Drive API lookup) on every
single tool invocation. Per gspread's docs (Context7), the Sheets API allows only
300 requests/60s per project and 60/60s per user, and the docs explicitly
recommend caching/`open_by_key` over `open(title)` for repeated access.

**Fix:** cached the authorized client and the opened spreadsheet handle at module
level (`_gspread_client`, `_spreadsheet_cache`). Cache is invalidated on
`gspread.exceptions.APIError` so a stale handle doesn't get stuck across retries.
`get_keyword_tool` now reuses the same cached client instead of duplicating the
auth code inline.

**Note:** switching to `open_by_key()` (documented as the more efficient/
unambiguous lookup) wasn't done — there's no `SPREADSHEET_ID` configured anywhere
in this repo, only the spreadsheet name. If you want that further optimization,
add the spreadsheet's ID (from its URL) as an env var and it's a small follow-up.

## Sanity API version inconsistency

**File:** `lib/sanity_adapter.py`

**Found:** the adapter mixed a dated API version (`v2021-03-25`, via
`self.base_url`) for mutate/query with the bare, unversioned `v1` alias for image
asset upload.

**Fix:** asset upload now builds its URL from `self.base_url`, so every request
this adapter makes targets the same, consistent, documented API version. Sanity's
own docs (Context7) show asset upload using a dated version identical in
structure to mutate/query — `v1` was a legacy alias, not the documented pattern.

**Not fully verified live** — no Sanity credentials available here. Recommend one
manual image-upload test before relying on this in production, since the old `v1`
comment noted it as "confirmed working" from prior debugging.

## GROQ query injection in the Sanity adapter

**File:** `lib/sanity_adapter.py`

**Found (via the `sanity-integration` skill + Context7):** `fetch_internal_links`,
`resolve_categories_to_refs`, and `ensure_document_exists` all built GROQ query
strings by interpolating agent/LLM-generated values (topic keywords, category
names, document ids) directly into the query text via f-strings, then URL-encoded
the whole query. Sanity's own docs are explicit that this is the wrong pattern:
*"Use GROQ parameters... This prevents GROQ injection and is preferred over
string interpolation."* A topic string containing a `"` character would either
break the query outright or, in principle, allow the filter expression to be
altered.

**Fix:** added `_build_query_endpoint(query, params)`, which passes values as
`$`-prefixed, JSON-encoded URL parameters (the HTTP-API equivalent of the JS
client's `client.fetch(query, {params})`) instead of interpolating them into the
query text. All three call sites were rewired to use it.

**Verified:** confirmed a value containing an embedded `"` and GROQ-syntax
fragment (`test" && evil==true`) round-trips through the new helper as a single,
literal, JSON-encoded string value — it can no longer break out of the query's
string literal. Full app boot smoke test still passes.

## FastAPI startup as import-time side effects

**File:** `main.py`

**Found:** registering the custom agent runner (`set_default_agent_runner`) and
disabling tracing happened as bare module-level statements. Per FastAPI's docs
(Context7), this pattern predates the `lifespan` context manager (introduced in
FastAPI 0.93.0, 2023) which is now the recommended way to run startup/shutdown
logic — it's easier to test (via `TestClient`) and keeps setup/teardown paired
instead of relying on import order. Separately, `load_dotenv()` was called
*after* the import of `blog_agent.blog_agents` (which constructs `Agent()`
objects that read API keys immediately) — it only worked because other modules
imported earlier happened to call `load_dotenv()` first, which is fragile,
accidental correctness rather than a guaranteed ordering.

**Fix:** moved `set_tracing_disabled(True)` and `set_default_agent_runner(...)`
into an `@asynccontextmanager lifespan(app)` handler passed to `FastAPI(lifespan=lifespan)`.
Moved `load_dotenv()` to the very top of `main.py`, before any project imports.

**Verified:** booted the app via `TestClient(main.app)` (which actually triggers
ASGI lifespan startup/shutdown, unlike a plain import) and confirmed
`agents.run.get_default_agent_runner()` returns our `custom_runner` instance only
*after* the lifespan context starts — proving the registration now happens at the
correct point in the app lifecycle rather than at import time.

## Tools marked `async def` that were fully synchronous

**Files:** `tools/tools.py` — `post_to_sanity_tool`, `fetch_internal_links_tool`

**Found:** both were declared `async def` but contained no `await` anywhere in
their bodies — they called the fully synchronous `SanityAdapter` methods and
`requests` directly. This mattered because of how the Agents SDK schedules tool
calls: as of SDK **0.8.0** (confirmed via Context7's release notes, and now
running here after the version bump below), *synchronous* function tools are
automatically dispatched via `asyncio.to_thread` so they can't block the event
loop — but a tool declared `async def` is assumed to already be
non-blocking and is awaited directly on the event loop. Declaring these two as
`async def` opted them **out of** the SDK's automatic thread-offload safety net
while doing fully blocking work (Sanity document creation, image upload/download)
directly on the event loop.

**Fix:** removed `async` from both — they're now plain `def`, which is the
SDK-idiomatic way to write a blocking tool post-0.8.0 and lets the SDK handle
thread-offloading automatically.

**Note:** `get_stock_image_tool`, `generate_image_tool` (which has real
`time.sleep(10)` polling, up to 30 times), `textstat_tool`, and
`grammar_check_tool` were already plain `def` — no change needed there; they were
already getting the automatic thread-offload.

**Verified:** confirmed both functions' underlying callables are non-async
(`asyncio.iscoroutinefunction` returns `False`), which is what makes the SDK
route them through `asyncio.to_thread`.

## Dependency modernization: openai-agents 0.3.1 → 0.19.2

**Files:** `pyproject.toml`, `requirements.txt`, `uv.lock`

**Found:** the SDK was pinned/locked at `0.3.1` against a latest of `0.19.2` — a
16-minor-version gap. This mattered concretely for two of the fixes above: the
automatic sync-tool thread-offload (SDK 0.8.0+) didn't exist at 0.3.1, and the
newer `ModelRetrySettings`/`retry_policies` API (declarative retry/backoff
policies) isn't available on the old pin either.

**Fix:** ran `uv lock --upgrade-package openai-agents` (pulled `openai` 1.108→
2.52, `pydantic` 2.11→2.13, `mcp` 1.12→1.29, and a few transitive deps along with
it, as a consistent resolved set) and `uv sync`. Bumped the floor in
`pyproject.toml`/`requirements.txt` to `>=0.19.2` to document the intended
minimum.

**Verified:** every project module imports cleanly under the new SDK
(`tools.*`, `lib.*`, `blog_agent.*`); the full `main.py` FastAPI app boots via
`TestClient` with all routes registered and the custom runner wired correctly.
**Not live-tested** — no API keys available here, so an actual agent run
(`/research`, `/generate_brief`, etc.) against real Gemini/OpenRouter/Cohere/
Sanity/Sheets endpoints hasn't been exercised. Recommend running one real request
against each endpoint before trusting this in production.

## Environment notes

- Running `uv sync` from this WSL shell rebuilt `.venv` as a Linux venv
  (`.venv/lib/python3.13`), replacing the previous Windows venv
  (`.venv/Scripts/python.exe`). `.venv` is gitignored and disposable — just
  re-run `uv sync` from your normal Windows shell if you need that back.
- Nothing here was committed to the working-directory `master` branch. The
  working tree already had ~20 files modified before this audit started
  (`.gitignore`, `Dockerfile`, `docs/*`, etc.) that weren't touched or reviewed
  as part of this work — commit review/staging was left for you to do
  separately.

## Skills/tools used for this audit

- **Context7 MCP** — current docs for `openai-agents-python` (Runner/handoff
  semantics, `ModelRetrySettings`, sync-tool threading), `gspread` (quota limits,
  `open_by_key`, `batch_update`), `tavily-python` (`AsyncTavilyClient`), `sanity.io`
  (HTTP mutate/query API, GROQ parameters, asset upload versioning), `fastapi`
  (`lifespan` pattern).
- **Tavily MCP** — SDK version history / release notes for `openai-agents`,
  cross-provider fallback discussion in the SDK's own issue tracker.
- **`sanity-integration` skill** — mostly Next.js/TypeScript-oriented and not
  directly applicable to this Python/REST backend, but its GROQ reference
  surfaced the parameterized-query pattern that fixed the injection issue above.

## Trigger mechanism migration: cron-job.org/FastAPI → GitHub Actions + Discord

**Problem:** cron-job.org hitting FastAPI endpoints synchronously had two real
failure modes — the cron service's own request timeout could cut off a
workflow that legitimately takes minutes (image generation alone polls for up
to 5 minutes), and nothing prevented two triggers from overlapping and racing
on the same Google Sheet rows.

**Fix:** GitHub Actions (`.github/workflows/pipeline.yml`) now runs the
pipeline directly — no HTTP hop, no server process, no timeout risk, since
the workflow *is* the execution environment. A new Discord bot
(`discord_bot/`) replaces manual sheet editing for approvals (react ✅/❌ on
the draft-preview message, posted via `DISCORD_WEBHOOK_URL` after the
`content` stage) and replaces curl-ing FastAPI endpoints for on-demand runs
(`/run` slash command → GitHub's `workflow_dispatch` API). Deployed to the
existing Hetzner VPS via Dokploy, following the same GHCR build → Dokploy
webhook-deploy pattern used elsewhere on that VPS (adapted for a background
worker with no exposed port — confirmed via Dokploy's own docs/community
threads that this is a standard, supported pattern, not a workaround).

**Bug found during migration:** `main.py`'s `/run_workflow` endpoint imports
`run_content_generation_workflow` from `blog_agent.blog_agents` — that
function doesn't exist anywhere in the codebase (verified via `grep`). The
`"content"` and `"full"` branches of that endpoint would have raised an
`ImportError` if ever actually exercised. `scripts/run_stage.py` (the new
entrypoint) replicates the actually-working pattern from `/generate_content`
instead: `custom_runner.run_with_fallback(content_generator_agent, "...", max_turns=MAX_TURNS)`.

**New reliability addition (bot.py):** a connection watchdog tracks time
since the last gateway event (via `on_socket_event_type`, fired on every
websocket message) and force-restarts the process (`os._exit(1)`, letting
Dokploy's restart-always policy bring up a fresh connection) if the socket
goes silent for 120s+ — the gateway can report itself as "connected" while
having actually stopped receiving events, which discord.py's own reconnect
logic doesn't self-heal from since it doesn't know anything is wrong. Added
per a direct request to mirror the health-monitor/restart pattern used by
OpenClaw (`openclaw/openclaw`) for its own Discord channel, after confirming
via that project's issue tracker what specifically their supervisor does
(restart the process on a stale/disconnected gateway) rather than assuming.

**Verified:** `scripts/run_stage.py` and `discord_bot/bot.py` both compile
and import cleanly; the watchdog's staleness-detection logic was tested
directly (fresh timestamp → healthy, backdated timestamp beyond the 120s
threshold → correctly flagged stale) without a live Discord connection, which
isn't available in this environment. **Not live-tested:** actual Discord
gateway connection, `workflow_dispatch` dispatch, and a real Dokploy deploy —
no bot token, GitHub PAT, or Dokploy credentials available here. See
`docs/service_setup.md`'s "Discord bot + GitHub Actions setup" section for
the end-to-end verification steps to run once secrets are in place.

## Live testing round: real bugs found once actually deployed

Once the bot and pipeline were live, three real issues surfaced that no
amount of local/offline testing could have caught:

1. **`astral-sh/setup-uv@v8` doesn't exist as a tag** (only fully-specified
   versions like `v8.3.2`, or newer `v9.0.0`) — `pipeline.yml`'s first real
   run failed at "Set up job" before any code executed. Verified the actual
   available tags via `gh api repos/astral-sh/setup-uv/tags` rather than
   guessing again; also bumped `actions/checkout`, `docker/setup-buildx-action`,
   `docker/login-action`, `docker/build-push-action` to their current major
   tags while fixing this (confirmed each one actually exists first).
2. **`scripts/run_stage.py` raised `ModuleNotFoundError: No module named
   'blog_agent'` under the real `python scripts/run_stage.py` invocation.**
   Python puts a script's own directory on `sys.path[0]` when run by path,
   not the repo root — `blog_agent`/`tools`/`lib` are siblings of `scripts/`,
   not inside it. Every local test this session used `python -c
   "sys.path.insert(0, '.'); import ..."` from the repo root, which never
   exercises this failure mode — only actually running the script the way
   the workflow does caught it. Fixed with an explicit `sys.path.insert(0,
   repo_root)` at the top of the file, confirmed by reproducing the exact
   failing invocation locally before pushing again.
3. **`gemini-2.5-flash` and `gemini-2.5-flash-lite` both 404'd** against the
   real `GEMINI_API_KEY` with "no longer available to new users" — see the
   "Fix live Gemini 404s" entry above.

Takeaway worth keeping in mind for future changes to this pipeline: nothing
here was catchable by import checks or `TestClient` smoke tests alone. A
`.github/workflows/check-models.yml` diagnostic now exists specifically so
model-availability regressions are a 30-second manual check instead of a
live pipeline failure discovered after the fact — but for genuinely new
code paths (a new workflow, a new script entrypoint), there's no substitute
for actually triggering it once for real before considering it done.

## Added: `/add_topic` Discord command

Added a third bot responsibility beyond approvals and triggering: `/run` and
reactions cover the pipeline's existing flow, but there was no way to get a
*new* topic into the queue without manually editing the
`ContentSpark_Keywords` sheet. `/add_topic` (`discord_bot/bot.py`) accepts
either a short `text` concept/problem or a `.txt` `file` attachment (for
content too long for a Discord text field, like a full video transcript) and
appends it as a new `available` row — same effect as adding the row by hand,
just from Discord. Considered adding this as a FastAPI endpoint instead, but
`main.py` isn't deployed anywhere anymore (fully replaced by GitHub Actions +
the bot), so a direct Sheets write from the bot — consistent with how
reactions already work — avoided standing up a server for no other reason.

Also fixed while touching this file: `GITHUB_REPO` was set to a full
`https://github.com/...` URL in Dokploy instead of `owner/repo`, which broke
`/run`'s GitHub API call with a 404 (confirmed live, from the bot's own error
message in Discord). `bot.py` now strips a `https://github.com/` (or
`http://`) prefix and trailing `.git`/slash automatically, so this exact
mistake doesn't recur even if the env var is set the "wrong" way again.

## Second live-testing round: false-positive failures, quota, and Discord UX

More real GitHub Actions logs surfaced two bugs that made successful runs
look like failures, plus a batch of feature requests to make Discord the
actual operating surface instead of just a notifier.

1. **`scripts/run_stage.py` marked successful brief/content runs as
   failed.** Both agents always return JSON containing an `"errors": []`
   key, even on success. The failure check was `"error" in output.lower()`,
   which matches the literal substring `"errors"` inside that key name —
   every successful run tripped it, silently skipping the Discord draft
   notification and showing red in Actions despite `"status": "success"` in
   the actual payload. Fixed by parsing the JSON (stripping a ```json fence
   if present) and checking the real `status` field
   (`_agent_output_indicates_error`) instead of substring-matching.
2. **Gemini quota assumed too high.** `model_limits["gemini"]` was `50`;
   a live 429 response showed `quotaValue: '20'` — the real daily limit.
   Lowered to `20` so the fallback chain (Gemini → OpenRouter free →
   Cohere) rotates before Gemini actually starts erroring, instead of after.
3. **Discord approval messages only showed the title/summary**, requiring a
   trip to the sheet to read the actual post. `_notify_discord` in
   `scripts/run_stage.py` now also posts the full `Generated Content` and
   `FAQs` as follow-up messages, chunked on paragraph/line boundaries to
   stay under Discord's 2000-char message cap (`_chunk_for_discord`) — the
   original title/summary/react message is unchanged so the bot's
   reaction-matching logic still works.
4. **No stage ever reported status to Discord beyond the content-draft
   notification** — a failed `research`/`brief`/`post` run was silent
   outside the Actions log. `main()` now calls a new
   `_notify_discord_status(stage, success, detail)` after every stage,
   success or failure (skipped for `content` on success since it already
   gets the richer draft notification).
5. **Author context was fully hardcoded** in `get_author_context_tool`
   (`tools/tools.py`) — bio, current job, skills never changed as the
   portfolio site's own data did. It now fetches
   `https://owaisabdullah.dev/api/profile` live and merges the current
   `about`/`summary`/`current_roles`/`skills`/`key_highlights` into the
   returned context under a `live_profile` key, falling back to the
   original static context if the request fails. The static fields (tone,
   banned words, CTAs) stay hardcoded since those are a style guide, not
   biographical fact that goes stale.
6. **Content-generation instructions strengthened against AI-sounding
   writing.** Added a condensed "Anti-AI-Pattern Checklist" to
   `content_generator_agent`'s prompt (`blog_agent/blog_agents.py`),
   distilled from the repo's `humanizer-main` skill (Wikipedia's "Signs of
   AI writing" guide): no inflated-significance phrases, no copula
   avoidance ("serves as"/"stands as"), no rule-of-three padding, no vague
   attributions, no curly quotes, no signposting ("let's dive in"), prefer
   specifics over superlatives. `content_evaluation_agent` now also checks
   for these tells as part of its readability scoring. (`social-media-writer`
   wasn't a direct fit to fold in beyond this — it's tuned for short-form
   LinkedIn/Twitter posts, which this pipeline doesn't generate; its
   "specifics over superlatives, admit uncertainty" principles are the part
   that carried over.)
7. **Freepik 401 (`Unauthorized`) confirmed from live logs** — this is a
   credential problem (the `FREEPIC_API_KEY` secret is invalid/expired),
   not a code bug. `generate_image_tool` already fails gracefully (returns
   `{"error": ...}`, no exception), and `image_selection_agent`'s own
   instructions already fall back to `get_stock_image_tool` on generation
   failure, so posts keep publishing with a stock photo instead of an
   AI-generated one — but the AI-image feature is effectively off until the
   key is regenerated in the Freepik dashboard and the GitHub secret is
   updated. **This needs action from you**, not a code fix.
8. **Posting-chain marker fragility** — real logs showed `"Contextual agent
   did not return data with expected markers format"` followed by the
   Posting Agent failing to find data between the `=== POST_DATA_START/END
   ===` markers. Root cause: the Preparation → Contextual Image Insertion →
   Posting handoff chain relied on each LLM call echoing a full
   multi-thousand-word blog post back verbatim between literal text
   markers. Fallback models (Cohere, OpenRouter free tier) routinely
   mangled or dropped the markers when asked to pass that much text through
   unmodified — asking an LLM to be a lossless wire format for data it has
   no reason to touch is inherently fragile, and it gets more likely to
   fail the more often a run lands on a non-primary model.

   Fixed in `blog_agent/posting_agent.py` by parsing and rebuilding the
   marker block in Python instead of trusting either agent's raw text:
   `_parse_post_data_block` extracts `KEY: value` fields deterministically
   (tolerant of colons inside the content body itself), and
   `_build_post_data_block` reconstructs a guaranteed well-formed block
   before it's handed to the next agent. If the Contextual Image Insertion
   Agent drops the markers, the workflow now degrades gracefully — it keeps
   the original prepared content without the extra contextual images and
   continues — instead of failing the entire publish over a cosmetic
   image-placement step. The Posting Agent now always receives a
   Python-built block, removing that hop as a failure point entirely.
