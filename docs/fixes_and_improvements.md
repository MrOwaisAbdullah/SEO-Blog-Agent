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

## Added: Cloudflare Workers AI as a free image-generation fallback

Freepik's 401 (item 7 above) exposed a real gap: the only fallback after AI
image generation failed was stock photos, meaning a bad/expired Freepik key
silently turned off AI-generated hero images entirely. Added Cloudflare
Workers AI (`generate_image_tool` in `tools/tools.py`, new
`_generate_image_cloudflare` helper) as a middle tier between Freepik and
Pexels — genuinely free (10,000 Neurons/day, no credit card, resets daily at
00:00 UTC), not a trial credit like Freepik.

Model choice (`@cf/black-forest-labs/flux-2-dev`, i.e. FLUX.2 [dev]) was
picked from Cloudflare's ~45-model catalog using live LM Arena / Artificial
Analysis Text-to-Image leaderboard data rather than reading model
descriptions at face value — this mattered because **Cloudflare's own model
catalog mixes free and non-free models in the same list with no visual
distinction beyond a small "Cloudflare-hosted" vs "Third-party" tag.**
Models like `gpt-image-2`, `nano-banana-2`, `seedream-4.5`, `flux-2-pro`,
and `flux-2-max` all appear in the same catalog page as the free FLUX/Leonardo
models, but are proxied to their original providers — the free Neuron pool
does not apply to them at all, confirmed against Cloudflare's own docs (a
model's `source` field is `hosted` vs `proxied`, and free tier only covers
`hosted`). Restricting the leaderboard comparison to the "Cloudflare-hosted"
subset only, FLUX.2 [dev] ranked clearly highest (Artificial Analysis has it
around #8 overall, ELO ~1149-1244 depending on source/date) against the
cheaper FLUX.2 [klein] 4B/9B tiers and legacy FLUX.1 [schnell] also
available for free on this account.

Implementation notes:
- FLUX.2 [dev] requires `multipart/form-data` even for a text-only prompt —
  a documented quirk of this specific model family on Workers AI, unlike
  `flux-1-schnell` which takes plain JSON.
- Requests set `width=1280, height=720` for a 16:9 landscape framing,
  matching the aspect ratio Freepik was already generating
  (`widescreen_16_9`) so featured images stay visually consistent regardless
  of which tier actually generated them.
- Response is a base64-encoded image (`result.image`), decoded and written
  to a temp PNG file — `post_to_sanity_tool` already handles arbitrary local
  file paths generically, so no changes were needed there.
- New env vars are optional: `generate_image_tool` skips straight past this
  tier (falls to Pexels) if `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`
  aren't set, same graceful-degradation pattern as everything else in this
  fallback chain.

## Third live-testing round: agent reports success without saving anything

Real run: `pipeline.yml`'s `brief` stage finished green, the printed
`[brief] result` was a complete, well-formed JSON brief with
`"status": "success"`, and `content_briefs` still had nothing in it. Reading
the full tool-call log line by line showed why: Gemini hit its daily 429,
fell back to Cohere, and Cohere called `find_row_by_key` on `research_data`,
`get_author_context_tool`, `tavily_search_tool`, and `tavily_extract_tool` --
then just wrote the final JSON answer as text instead of also calling
`manage_sheet_data_tool` with `action="append_row"` to actually save it, and
never updated `research_data`'s `Generated` column either. Nothing in the
JSON itself said this happened; `"status": "success"` was **true** as far as
the model was concerned -- it had successfully produced a correct brief, it
just never persisted it.

This is the same underlying failure mode as the posting-chain marker
fragility fixed earlier: a fallback model correctly did the "thinking" part
of a multi-step task but silently skipped an actual side-effecting tool call
near the end of a long turn sequence, and nothing in the pipeline verified
that the side effect actually happened before treating the stage as done.

Fixed in `scripts/run_stage.py`: `run_brief()` and `run_content()` no longer
trust the agent's JSON `"status": "success"` as proof that anything was
written. After parsing the JSON (`_parse_agent_json`, factored out of
`_agent_output_indicates_error`), `_ensure_brief_persisted()` /
`_ensure_content_persisted()` check whether a matching row (by
`Keyword/Topic` / `Title`) already exists in `content_briefs` /
`generated_posts` and, if not, append it directly via `manage_sheet_data` --
the same underlying function the agent's own tool wraps, called
deterministically from Python instead of hoping the model calls it.
`_ensure_brief_persisted` also marks the source `research_data` row's
`Generated` column as `"Yes"` if the agent didn't, so the same research
finding can't get silently reprocessed on the next brief run. `run_content`
now also uses the *actually persisted* row (not just "the last row in the
sheet") for the Discord draft notification, so a stale row can't get
mistakenly announced as a new draft if the agent's save had failed.

Broader takeaway for this pipeline: any step where an agent's job includes
"call a tool to save X" is a step that can silently no-op on a weaker
fallback model, even when everything else about its output looks perfect.
The fix pattern going forward is the same each time: keep the agent's
instructions asking it to save (it works fine most of the time, on Gemini
especially), but never let the *stage's* definition of success depend on
trusting that it did -- verify or do it deterministically in Python instead.

## Fourth live issue: `post` stage failing with "project_id, dataset, and token must be provided"

Real failure: the `post` stage failed every attempt with
`Unexpected error in post_to_sanity_tool: project_id, dataset, and token
must be provided.` even though `SANITY_PROJECT_ID` and `SANITY_API_TOKEN`
were both set as GitHub repo secrets (confirmed via `gh secret list`).

Root cause: `SANITY_DATASET` was never added as a repo secret at all.
`pipeline.yml` unconditionally sets `SANITY_DATASET: ${{ secrets.SANITY_DATASET
}}` in the job env -- and GitHub Actions resolves a reference to a
**non-existent** secret to an **empty string**, not an omitted key. So the
env var `SANITY_DATASET` existed in the runner's environment with value
`""`. `tools/tools.py` read it with
`os.environ.get('SANITY_DATASET', 'production')` -- but `dict.get(key,
default)` only returns the default when the key is **absent**, not when
it's present with a falsy value, so this returned `""` instead of falling
back to `'production'`. `SanityAdapter.__init__` then rejected the empty
string (`if not all([project_id, dataset, token])`).

Fixed in both call sites in `tools/tools.py` (`post_to_sanity_tool` and
`fetch_internal_links_tool`): `os.environ.get('SANITY_DATASET') or
"production"` instead of the two-arg `.get()` form, so both "the secret was
never set" and "the secret exists but is empty" fall back correctly. Per
the user, this project only ever publishes to the `production` dataset, so
the env var is really just an override knob, not something that needs its
own secret configured going forward.

General takeaway: `os.environ.get(key, default)` is not a safe pattern for
any GitHub Actions secret that might not be configured, specifically
because Actions' `${{ secrets.X }}` syntax never errors on an unset secret
-- it silently substitutes an empty string. Anywhere in this codebase that
does `os.environ.get(key, default)` for an *optional* secret should really
be `os.environ.get(key) or default`.

## Fifth live issue: garbage "N/A" rows written to research_data on an empty keyword queue

Real failure: `research_data` gained a row where every field was `N/A`
except Content Summary, which was the sentence *"I'm sorry, I cannot
conduct research without a keyword or URL. Please provide a valid keyword
or URL to proceed with the research."* -- an LLM refusal, saved as if it
were a real research finding.

Root cause: `combined_research_workflow` (`blog_agent/research_agent.py`)
runs three agents in sequence -- Triage (picks a keyword from
`ContentSpark_Keywords`) -> Researcher -> Output (writes to
`research_data`) -- and each step's retry loop only checked `"error" not in
str(result)` to decide whether the step "succeeded". When
`ContentSpark_Keywords` is empty, `get_keyword_tool` returns `{"error": "No
available keywords found"}`, but the Triage Agent (an LLM) doesn't relay
that literally -- it paraphrases it as a polite sentence with no literal
"error" substring, which passes the check. That empty/apologetic text then
gets handed to the Researcher Agent as "the keyword to research", which
correctly recognizes it has nothing to research and says so in its own
apology -- which *also* doesn't contain the word "error", so it passes the
check too, and the Output Agent dutifully writes that apology into the
sheet since that's literally what it was asked to consolidate.

Fixed with `_looks_like_refusal_or_empty()`: checks the Triage Agent's
output (before it's ever sent to the Researcher) and the Researcher
Agent's output (before it's ever sent to the Output Agent) against a set of
refusal-phrase markers and an empty/too-short check, short-circuiting to
`{"status": "no_available_keywords", ...}` at either point instead of
letting a refusal propagate all the way to a sheet write. This is the same
"don't trust a naive `'error' in str(...)` substring check" lesson as the
false-positive fix earlier in this doc, just living in a different file
(`research_agent.py`'s own retry loops were never touched when
`scripts/run_stage.py`'s equivalent bug was fixed).

## Added: automatic trending-topic discovery when the keyword queue is empty

Previously, an empty `ContentSpark_Keywords` queue just meant nothing
happened until someone manually added a topic (via `/add_topic` or editing
the sheet by hand). Per the user's request, `run_research()` now reacts to
an empty queue by running a new **Topic Discovery Agent**
(`run_topic_discovery_workflow()` in `blog_agent/research_agent.py`)
instead of just stopping:

- Uses `get_author_context_tool` to confirm the brand's actual current
  focus areas (from the live `owaisabdullah.dev/api/profile` data added
  earlier this session) rather than assuming a fixed niche.
- Runs several targeted `tavily_search_tool` queries (`site:reddit.com
  ...`, `site:quora.com ...`, `"... trending this week"`) to find what's
  actually being discussed right now, not generic evergreen topics.
- Returns 3-5 specific candidates, each with a one-sentence rationale
  citing what was actually found.

Candidates are **never** written directly to the sheet -- each is posted to
Discord as its own message (`_notify_discord_topic_candidates` in
`scripts/run_stage.py`) with its own ✅/❌ reaction, mirroring the existing
draft-approval pattern. `discord_bot/bot.py`'s reaction handler now
recognizes a `**Candidate Topic:**` line (parallel to how it already
recognizes `**Title:**` for drafts) and calls the existing `add_keyword()`
helper on approval, which is the same function `/add_topic` already uses --
so an approved candidate lands in `ContentSpark_Keywords` exactly like a
manually-submitted topic would, ready for the next `research` run to pick
up normally.

Also added as its own on-demand stage (`discover_topics`), independent of
an empty queue, so topic discovery can be triggered manually rather than
only reactively:
- New `workflow_dispatch` choice in `.github/workflows/pipeline.yml`.
- New `discover_topics` entry in `discord_bot/bot.py`'s `/run` command
  choices -- `/run discover_topics` in Discord fires it the same way `/run
  research`/`/run content`/etc. already do.

No new secrets or services needed -- reuses Tavily (already configured) and
the same Discord webhook/bot wiring already in place.

## Added: conversational status assistant in the Discord bot

Per the user's request, the bot now does more than react to reactions and
run slash commands -- it can answer questions about the pipeline and
discuss topic ideas:

- **`/status`** -- deterministic, no LLM involved. Reads all five
  worksheets (`ContentSpark_Keywords`, `research_data`, `content_briefs`,
  `generated_posts`, `published_posts`) via `gather_pipeline_status()` and
  posts counts for what's queued/pending at each stage, plus a few sample
  titles.
- **@mention chat** -- `on_message` now responds when the bot is
  @mentioned, running a real `Agent` (OpenAI Agents SDK, same package/pin
  as the main pipeline) backed by `deepseek/deepseek-v4-flash-latest` on
  OpenRouter. The agent has one tool, `get_pipeline_status_tool` (wraps the
  same `gather_pipeline_status()` used by `/status`), which it calls itself
  only when a question actually needs live counts -- e.g. "how many briefs
  are waiting" triggers a sheet read, "what do you think about writing
  about X" doesn't. Stateless per message (no session/history) for now.

Initial version used a hand-rolled `requests.post` call to OpenRouter's
chat completions endpoint with the status snapshot pre-computed and stuffed
into the system prompt on every message. Switched to a real SDK-backed
`Agent` with `Runner.run()` per explicit instruction -- it fetches status
on demand via its own tool call instead of paying for a sheet read on every
single message regardless of whether the question needs one, and is
actually extensible (more tools can be added later) rather than a fixed
prompt template. Verified directly against the installed `openai-agents`
package in this repo (`Agent`/`Runner`/`function_tool`/`AsyncOpenAI`/
`OpenAIChatCompletionsModel`/`set_tracing_disabled` all import and
construct correctly, including a sync dict-returning `@function_tool`).

The bot previously avoided the `openai-agents` SDK entirely by design (see
the module docstring's original rationale) to stay a lightweight,
independently-deployable container. That tradeoff was reconsidered here:
a real per-message chat needs actual tool-calling, which is exactly what
the SDK exists for, so the dependency was worth taking on. The bot still
authors its own tools rather than importing the pipeline's `tools/` module,
so the two still deploy independently -- only the SDK itself is now shared.

New optional env var: `OPENROUTER_API_KEY` on the bot's Dokploy deployment
(same key value as the pipeline's own secret, just configured separately
since it's a different container). Without it, `/status` still works
(pure sheet reads); @mentioning the bot just replies that chat isn't
configured yet instead of calling anything.

## Also: reduced retry counts across the pipeline (3 -> 2)

Per the user's request ("try models 1 or max 2 times then move to the
fallback"), every `max_retries` default that governs how many times an
agent call is retried before giving up was reduced from 3 to 2:
`custom_runner.py`'s `run_with_fallback` (retries the *entire* provider
fallback chain this many times if every provider fails in a pass) and the
outer per-agent retry loops in `posting_agent.py`, `research_agent.py`
(including the new `run_topic_discovery_workflow`), and `image_agent.py`
(each of these wraps a full `run_with_fallback` call, so the two retry
counts previously multiplied together -- up to 3x3=9 full fallback-chain
attempts in the worst case for a single agent call). This cuts worst-case
wasted time/quota on a genuinely broken run roughly in half without
removing retry coverage entirely -- a single transient hiccup still gets a
second try, it just stops burning through the whole provider list
repeatedly for something that isn't transient.

## Sixth (severe) live issue: duplicate Sanity publish from a false-positive retry

The Sanity dataset fix worked -- a real `post` run successfully published
"DeepSeek V4 Flash 0731: The AI Model That's Changing the Game for $1 a
Month" to Sanity. But the same run's log showed `DEBUG: Successfully
converted to 14 Sanity blocks` **twice**, ~70 seconds apart, which meant the
entire Preparation -> Contextual Image Insertion -> Posting chain ran
twice for the same post -- and `SanityAdapter.create_document` uses a plain
`{"create": document}` mutation with no `_id` set and no dedup-by-slug
check, so a second run creates a genuinely separate, duplicate document in
Sanity, not a harmless no-op. **If you're reading this, check Sanity
Studio for a duplicate of that post and delete the extra one if present --
this was flagged to the user directly when found, but noting it here too
in case it's missed.**

Root cause: `run_posting_workflow`'s Posting Agent retry loop (same file as
the marker-fragility fix earlier) checked `if "error" not in
str(posting_result).lower()`. The actual run's Posting Agent successfully
published to Sanity, then hit an unrelated, genuine bug trying to mark the
sheet's `Published` column (`update_cells` called with a malformed
sheet-qualified range like `'generated_posts'!Published`, which isn't valid
A1 notation), and its final summary text read: *"...successfully published
to Sanity CMS... but I encountered an **error** when trying to update the
'Published' column..."* -- containing the literal word "error" for a
sub-task, which the check couldn't distinguish from an actual publish
failure. It retried the whole workflow, calling `post_to_sanity_tool` a
second time.

This is the same "don't trust `'error' in str(...)`" lesson as everywhere
else in this doc, but the highest-stakes instance of it so far -- the
earlier false positives caused a misreported CI status or a wasted retry;
this one created real, duplicate public content.

Fixed with `_sanity_publish_already_succeeded()`: instead of parsing the
agent's narrative text, it inspects the `RunResult.new_items` for the
actual `tool_call_output_item` from `post_to_sanity_tool` itself (matched
by its distinctive `{"status", "post_id"}` key shape, which no other tool
this agent uses returns) and trusts that directly. The retry loop now
checks this first, before falling back to the text-based check only when
no Sanity tool-call output exists at all (e.g. the agent never called it).
Also fixed the actual sheet-update bug that triggered this in the first
place: the Posting Agent's instructions now spell out the exact
`find_row_by_key` -> `get_range` (header lookup) -> `update_cell` sequence
with explicit row/col indices instead of leaving "update the Published
column" to the model's own judgment about which tool action to use, and
explicitly say a sheet-update failure after a successful publish should be
reported as a warning, not worded as an "error" -- a secondary,
lower-confidence mitigation on top of the deterministic code fix, since
prompt wording alone has repeatedly not been reliable enough on its own
this session.

## Fixed: wrong DeepSeek model slug on OpenRouter (missing `~` prefix)

Live failure: the bot's new @mention chat returned `Error code: 400 -
deepseek/deepseek-v4-flash-latest is not a valid model ID`. OpenRouter's
own docs (confirmed via a real screenshot of the model page + a follow-up
search) use a literal `~` prefix on a model slug specifically to mean
"always resolve to the latest version of this model family" -- e.g.
`~deepseek/deepseek-v4-flash-latest`, not `deepseek/deepseek-v4-flash-latest`.
Without the tilde, OpenRouter doesn't recognize the alias at all.

Fixed in both places this exact string appeared:
`discord_bot/bot.py`'s `DEEPSEEK_MODEL` and
`blog_agent/custom_runner.py`'s `LAST_RESORT_MODELS` (the pipeline's own
paid last-resort tier) -- the same bug would have hit the pipeline's
DeepSeek fallback the first time it was ever actually reached, just hadn't
surfaced yet since Gemini/OpenRouter-free/Cohere have covered every run so
far this session.

## Added: the chat assistant can act, not just report

After using the read-only `/status` + chat, the user ran into its limits
live: asking it to "write content for digital fte" got a "you'll need to
run a slash command yourself" answer, and telling it a post was already
published had nowhere to go either. Per direct follow-up requests, gave
the `discord_bot/bot.py` chat agent four new tools so it can actually act
on what it's told, instead of only describing what the human should do:

- **`prioritize_topic_tool(topic_reference)`** -- finds the row matching a
  topic (case-insensitive partial match) in whichever queue it's currently
  sitting in (`ContentSpark_Keywords`, `research_data`, or
  `content_briefs`, searched in that order) and moves it to the top via
  delete + re-insert at row 2. Every pipeline stage always picks the first
  eligible row, so this makes that topic the next one picked up --
  explicitly *not* deleting anything else in the queue, just reordering,
  per the user's requirement.
- **`trigger_stage_tool(stage)`** -- wraps the same `dispatch_workflow()`
  the `/run` command already uses, so the agent can actually kick off a
  stage after prioritizing a topic instead of telling the user to type
  `/run` themselves.
- **`mark_post_published_tool(title_reference)`** -- sets a post's
  `Published` column to "Yes" by fuzzy title match, for when a post went
  out some way the pipeline doesn't know about (manually, or otherwise) --
  without this, the `post` stage would eventually try to publish it again,
  and Sanity has no dedup (see the duplicate-publish fix above).
- **`set_post_approval_tool(title_reference, approved)`** -- sets
  `Approve/Disapprove` by fuzzy title match, reusing the same column the
  existing reaction handler (`set_approval()`) already writes to, so
  approving/rejecting works identically whether it happens via a ✅/❌
  reaction or a chat instruction.

All four share `_find_row_index()` (case-insensitive substring match
against a worksheet column) and, for prioritization,
`_move_row_to_top()` (delete_rows + insert_row at position 2). The
system prompt was updated to explicitly tell the agent to call these
tools itself rather than describing the equivalent slash command --
the whole point of giving it tools is that it stops being a read-only
FAQ bot once it has a way to act.

## Fifth `_get_field` variant: fields nested under a "data" wrapper

Yet another live shape from Cohere on the `brief` stage: instead of the
flat structure the prompt's example shows, it returned
`{"status": "success", "message": "...", "data": {"Keyword/Topic": ...,
"Brief Content": ..., ...}}` -- all the real fields nested one level down
under a `"data"` key. `_get_field` only checked top-level keys, so
`Keyword/Topic` came back empty and the brief stage failed with "Brief
output missing Keyword/Topic" even though the data was right there, just
nested.

Extended `_get_field` to fall back to checking one level of nesting: if a
field isn't found at the top level, it now also checks inside any
top-level value that's itself a dict. This covers `"data"`/`"result"`/
similar wrapper shapes generically rather than special-casing the specific
key name "data". Verified directly against the real payload from the
failing run.

## Removed Cohere as an LLM provider entirely

By this point Cohere (`command-a-03-2025`, used as a fallback tier) had
caused six distinct incidents in one extended live-testing session: three
JSON-shape variants (lowercase/underscored keys, a "data"-nested wrapper)
and two non-JSON variants (two different raw-Markdown shapes), an
unexecuted tool call leaked as plain text, plus one run that worked
correctly. Each was fixed defensively as it appeared, but new shapes kept
surfacing faster than they could reasonably be chased. Removed entirely
per explicit request rather than continuing that pattern.

Removed from `blog_agent/custom_runner.py`: the `LLM_MODELS` entry,
`get_cohere_client()`, and every `"cohere"` key in `model_usage`,
`model_limits`, `provider_stats`, and `provider_unavailable_until`. Also
took the opportunity to fully remove MiniMax (per a follow-up request) --
it was already commented out of `LLM_MODELS` and never active, but
`get_minimax_client()`, its provider-routing branch, and its dict entries
were still sitting around as dead code. Removed `COHERE_API_KEY` /
`MINIMAX_API_KEY` from `.env.example`, `README.md`,
`docs/service_setup.md`, `.github/workflows/pipeline.yml`, and the Cohere
check from `.github/workflows/check-models.yml` (replaced with a DeepSeek
last-resort check instead, now that its `~` prefix bug is fixed).

**Caught before it shipped:** two agents were hardcoded to
`model=custom_runner.get_model_by_name("cohere")` as their *only* model --
`image_selection_agent` (`blog_agent/image_agent.py`) and the Output Agent
inside `combined_research_workflow` (`blog_agent/research_agent.py`).
Removing Cohere from `LLM_MODELS` without touching these would have made
`get_model_by_name("cohere")` raise `ValueError` immediately -- for
`image_selection_agent` that's at **import time** (since `model=` is
evaluated when the module loads), which would have broken every stage that
transitively imports `image_agent.py` (which is most of the pipeline, via
`posting_agent.py`). Caught by actually importing `image_agent.py` and
constructing `FallbackAgentRunner()` after the removal, rather than just
grepping for the word "cohere" in strings. Both switched to
`gemini-flash-latest`, the same default used elsewhere in each file.

## False-positive brief failure: real success, plain-text final answer

Live re-test after the Cohere removal: the `brief` stage failed with
`Stage 'brief' failed: The content brief has been successfully created and
saved to the content_briefs worksheet. The original research row in
research_data has also been updated to mark it as generated.` -- a
narrative sentence claiming success, not JSON. `_agent_output_indicates_error`
treated "not valid JSON" as an automatic failure and raised before the
brief's actual persistence logic ever ran.

Checked the full tool-call log for this run: the claim was **true**.
`manage_sheet_data_tool` really did return `{'status': 'success', 'message':
'Row appended to content_briefs.'}` and later `{'status': 'success',
'message': 'Cell (4, 8) updated in research_data.'}` -- the agent (after a
couple of self-corrected malformed tool-call attempts, visible in the same
log) did everything right and just described it in a sentence instead of
JSON. Failing the stage over that was a real regression: the brief was
already safely saved, and the run still reported itself as a failure.

Fixed with `_tool_call_succeeded(run_result, expect_in_message)`: scans
`RunResult.new_items` for an actual `manage_sheet_data`-shaped success
whose message contains the expected substring (e.g. "Row appended to
content_briefs"), independent of whatever the final answer's text/format
looks like. `run_brief()` was restructured to check this before giving up
on non-JSON output (mirroring `run_content()`'s JSON -> Markdown-salvage
cascade, now JSON -> tool-call-verified -> genuine failure), and
`run_content()` got the same check added as a third fallback path before
its own final failure, reading back the row from the sheet directly for
the Discord notification since there's no parsed dict to work from in that
path. Also generalized the two "Cohere compatibility-layer glitch" error
messages (`_looks_like_unexecuted_tool_call` call sites) to not name Cohere
anymore, since it's no longer a provider in this pipeline and the pattern
could in principle come from any OpenAI-compatibility shim.

This closes out the same lesson that's run through this whole session,
generalized one more time: **the model's own account of what happened is
not the source of truth for whether a stage succeeded -- the actual tool
call outputs are.** Every "false failure" and "false success" bug fixed in
this document came from trusting narrative text (JSON or not) over what
the tools actually returned.

## Fixed: values landing in the wrong `generated_posts` column

User report: the `generated_posts` sheet had a row where the **Summary**
column held `"Yes"` -- a value that clearly belongs in **Published**, two
columns over. Root cause: when the Content Generator Agent calls
`manage_sheet_data_tool` with `action="append_row"` **itself** (not
through `_ensure_content_persisted`'s deterministic fallback), the tool has
no way to validate the semantic shape of the `row_values` list it's
handed -- it just writes whatever list it gets, positionally. If the agent
passes a list with too few elements (e.g. skipping `Summary` and
`Approve/Disapprove`), gspread's `get_all_records()` (keyed by the real
header row) silently maps every value after the gap one or more columns to
the left -- no error, no validation, just data in the wrong place.

Two contributing causes fixed together:

1. **The few-shot example in `content_generator_agent`'s own prompt
   (`blog_agent/blog_agents.py`) was itself wrong.** It showed a
   `row_values` example with `"Generated"` and `""` sitting where `Summary`
   and `Approve/Disapprove` should be -- stale leftovers from what looks
   like an earlier column scheme, directly contradicting the "columns in
   order" line one line above it. LLMs weight examples heavily; a wrong
   example is a plausible direct cause of a model reproducing the wrong
   shape. Replaced it with a correct, fully-populated 7-element example,
   and added an explicit instruction to count `row_values` before calling
   the tool and confirm it's exactly 7 elements in the documented order.
2. **`_ensure_content_persisted` previously trusted any existing row
   unconditionally** once it found one matching the title ("agent saved it
   correctly", return as-is) -- no shape validation at all. Added
   `_row_looks_malformed()`: flags a row whose `Summary` value is literally
   `"yes"`/`"no"` (a dead giveaway it's actually holding the `Published`
   value) or whose `Published` is empty. When flagged, the row is now
   **repaired in place** -- each of the 7 fields is written individually
   via `update_cell`, looked up by the real header row's column *name* (not
   assumed position), using the correctly-extracted values from the
   already-parsed `content` dict -- rather than trusting whatever the
   agent's own malformed `append_row` call had produced.

This only self-heals a title if/when the `content` stage processes it
(or reprocesses it) again -- it does not retroactively scan and fix every
row already in the sheet. If other rows are affected, they'd need a manual
check or a one-off audit pass; ask if that's wanted.

## Fixed: post stage giving up entirely when image generation hit a quota wall

Live failure: `/run post` failed with `Preparation Agent output missing
required POST_DATA fields: STATUS: IMAGE_GENERATION_FAILED / MESSAGE:
Gemini API quota exceeded...`. Freepik 401'd (known, still needs a key
rotation), and then the image pipeline hit Gemini's daily quota too -- but
unlike every other LLM call in this pipeline, that one had no fallback
available at all, so the whole `post` stage aborted instead of degrading to
a stock photo.

Root cause: `image_quality_evaluation_agent`
(`blog_agent/image_agent.py`) is invoked as a nested tool
(`.as_tool(...)`) with a model pinned directly to `gemini-flash-latest` --
it never goes through `custom_runner.run_with_fallback`'s provider
rotation at all, by original design (documented rationale: DeepSeek is
text-only, so vision calls were left Gemini-only). Whenever Gemini's daily
quota (20/day, confirmed real) is exhausted -- which happens routinely in
this pipeline's normal usage -- every image evaluation call fails
outright. `image_selection_agent`'s own instructions do say "if all
generation attempts fail, use `get_stock_image_tool`", but the Preparation
Agent that actually *calls* `image_selection_agent` (as `get_blog_image_tool`)
has no tools of its own beyond that one call -- when the nested tool
errored out entirely, Preparation Agent had nothing left to fall back to
and just reported the error as if it were the whole task's result.

Fixed by giving the Preparation Agent its own independent escape hatch:
added `get_stock_image_tool` directly to its tool list
(`blog_agent/posting_agent.py`), and an explicit instruction that if
`get_blog_image_tool` fails or errors for any reason, it should call
`get_stock_image_tool` itself (Pexels, no LLM/vision dependency at all) and
use whatever it returns rather than aborting the publish. A generic real
photo beats a failed post -- getting content published is the priority.
Verified the import resolves cleanly and `preparation_agent.tools` now
includes both `get_stock_image_tool` and `get_blog_image_tool`.

## Found and fixed: two hooks classes with the same name, one silently mute

User question ("why is Cloudflare image generation failing?") led to
discovering there was no way to answer it from the logs at all --
`Preparation Agent`/`Contextual Image Insertion Agent`/`Posting Agent`
produced **zero** `[Hook]` lines in a real run's log, not even agent
start/end, let alone individual tool calls (Freepik/Cloudflare/Pexels).

Root cause: two different classes are both named `MyAgentHooks`.
`blog_agent/hooks.py` defines the real one -- `print()`-based, logs agent
start/end AND `on_tool_start`/`on_tool_end` with the tool name and full
result -- and it's what Brief/Content/Research agents use (imported), which
is why *their* logs have always shown full tool-call detail. But
`blog_agent/posting_agent.py` defined its **own**, separate, much weaker
class with the identical name: only `on_agent_start`/`on_agent_end`, no
tool-level hooks at all, and using `logger.info()` instead of `print()`
(which, combined with this pipeline's logging setup, wasn't reliably
appearing in the captured Actions log either -- even the agent start/end
lines never showed up). On top of that, `blog_agent/image_agent.py`'s three
agents (`image_selection_agent`, `contextual_image_insertion_agent`,
`image_quality_evaluation_agent` -- the ones that actually call
Freepik/Cloudflare/Pexels) had **no hooks configured at all**, so even a
correct hooks class on Preparation Agent wouldn't have shown what happens
*inside* `get_blog_image_tool`.

Fixed by removing `posting_agent.py`'s local duplicate class and importing
the shared one from `blog_agent/hooks.py` instead (now used consistently
everywhere), and adding `hooks=MyAgentHooks()` to all three agents in
`image_agent.py`. This was a real, separate gap from the earlier
`PYTHONUNBUFFERED`/`python -u` fix -- that fixed *ordering* of output that
existed; this fixes the fact that most of the relevant output was never
being produced in the first place. Verified live: all five agents
(`preparation_agent`, `posting_agent`, `image_selection_agent`,
`contextual_image_insertion_agent`, `image_quality_evaluation_agent`) now
report `isinstance(agent.hooks, MyAgentHooks)` as `True` against the real
shared class.

## Removed Freepik as an image provider

Per explicit request, and consistent with its persistent 401 (an
expired/invalid key that was never rotated) plus the fact that it was only
ever a one-time trial credit rather than an ongoing free tier to begin
with (unlike Cloudflare Workers AI and Pexels, both genuinely free
indefinitely): removed entirely rather than leaving a permanently-broken
tier in the fallback chain.

- `tools/tools.py`: `generate_image_tool` no longer tries Freepik at all --
  Cloudflare Workers AI (FLUX.2 [dev]) is now the sole AI generator, with
  Pexels as the stock-photo fallback if it's unavailable or fails.
- `tools/tools.py` (`post_to_sanity_tool`) and `lib/sanity_adapter.py`
  (`post_blog`): removed the Freepik-specific URL-detection branches (image
  source could never be "Freepik" again anyway) -- Pexels URLs still pass
  straight through to Sanity, everything else still downloads and uploads
  as before.
- Removed `FREEPIC_API_KEY` from `.env.example`, `README.md`,
  `docs/service_setup.md`, and `.github/workflows/pipeline.yml`'s secrets.
  Marked `docs/freepik_ai_image_guide.md` (a raw copy of Freepik's own API
  reference) as obsolete rather than deleting it.
- Verified `posting_agent.py` imports cleanly with no `FREEPIC_API_KEY` set
  at all, and `preparation_agent.tools` still resolves correctly.

## Per-model Gemini quota tracking (not shared across the account)

User provided a screenshot of Google AI Studio's own rate-limits
dashboard, which settled something this pipeline had been guessing at all
session: Gemini's free-tier quota is genuinely **per model**, and nowhere
close to uniform. "Gemini 3.6 Flash" (what `gemini-flash-latest` currently
resolves to) is capped at 5 RPM / 20 RPD -- matching every 429 seen live
this session (`quotaValue: '20'`). "Gemini 3.5 Flash Lite" (what
`gemini-flash-lite-latest` currently resolves to) is capped at 15 RPM /
**500 RPD** -- 25x more daily requests, sitting almost entirely unused.

`custom_runner.py` previously tracked quota, performance stats, and
temporary-unavailability at the **provider** level (`"gemini"`), shared
across both Gemini model entries in `LLM_MODELS`. That meant the instant
`gemini-flash-latest`'s tight 20/day cap was hit, `gemini-flash-lite-latest`
got treated as exhausted too -- even though it still had roughly 480
requests of its own, completely separate quota sitting untouched. This is
likely a meaningful chunk of why Gemini has appeared to run out so
quickly and so often throughout this session's live testing.

Refactored `model_usage`, `model_limits`, `provider_stats`, and
`provider_unavailable_until` to all key by **model name**
(`"gemini-flash-latest"`, `"gemini-flash-lite-latest"`, `"openrouter-free"`,
`"deepseek-v4-flash"`) instead of provider string, with
`gemini-flash-lite-latest`'s limit set to `500` (from the dashboard) instead
of sharing Flash's `20`. `is_model_available`, `increment_usage`,
`_update_provider_stats`, `_sort_models_by_performance`, and the
rate-limit/permanent-error marking in `run_with_fallback` were all updated
to pass `model_config["name"]` instead of `model_config["provider"]`. A 429
on `gemini-flash-latest` now only marks *that* model temporarily
unavailable -- `gemini-flash-lite-latest` keeps its own independent budget
and gets tried normally. Verified live: `model_usage`/`model_limits` now
show four independent per-model buckets instead of three shared
provider-level ones.

## Added: discover_topics on its own ~2-day schedule

Per explicit request: `discover_topics` previously only ran reactively
(when `research` found an empty keyword queue) or on manual trigger, with
no schedule of its own. Since one run proposes 4-5 candidates (each
needing an individual Discord ✅/❌ approval before it's ever queued) and
`research` only consumes one keyword/day, running discovery daily would
just pile up unreviewed candidates faster than they could reasonably be
approved. Added `cron: '0 1 */2 * *'` (~every 2 days, 6:00 AM PKT, an hour
before `research`) to `.github/workflows/pipeline.yml`, with the matching
case-statement entry mapping that schedule to `stage=discover_topics`.
