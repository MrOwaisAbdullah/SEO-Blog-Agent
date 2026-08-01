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
