# Service Setup Guide

Every credential and external connection this pipeline needs, verified directly
against the code (not just the original README, which had drifted — see
[Corrections to README.md](#corrections-to-readmemd) at the bottom). Set
everything as environment variables — never as committed files. Locally, copy
`.env.example` to `.env` in the project root (already gitignored).

**Where things actually run now:** the pipeline no longer runs on Hugging
Face Spaces / FastAPI as the active trigger path — it runs via **GitHub
Actions** (`.github/workflows/pipeline.yml`), on a schedule and via
`workflow_dispatch`. Approvals and manual triggering go through a **Discord
bot** (`discord_bot/`), deployed on the existing Hetzner VPS via **Dokploy**.
`main.py`/the HF Spaces Dockerfile are left in the repo, untouched, but
unused — see [Discord bot + GitHub Actions setup](#discord-bot--github-actions-setup)
below for the credentials this needs. Everything in the table below still
applies — it's what the *pipeline itself* needs regardless of what triggers
it; it just needs to be set as **GitHub Actions repo secrets** now instead of
Hugging Face Spaces secrets.

## Quick reference

| Env var | Service | Required? |
|---|---|---|
| `API_KEY` | This app's own FastAPI auth | Required |
| `GOOGLE_CREDENTIALS` | Google Sheets (pipeline's database) | Required |
| `GEMINI_API_KEY` | Gemini (primary LLM) | Required |
| `OPENROUTER_API_KEY` | OpenRouter (LLM fallback, via `openrouter/free` auto-router) | Required |
| `COHERE_API_KEY` | Cohere (LLM fallback) | Required |
| `TAVILY_API_KEY` | Tavily (research search/extract/crawl) | Required |
| `SERPAPI_KEY` | SerpAPI (search fallback) | Required |
| `PEXELS_API_KEY` | Pexels (image fallback) | Required |
| `FREEPIC_API_KEY` | Freepik (primary AI image generation) | Required |
| `SANITY_PROJECT_ID` | Sanity CMS | Required |
| `SANITY_DATASET` | Sanity CMS (e.g. `production`) | Required |
| `SANITY_API_TOKEN` | Sanity CMS (needs write access) | Required |
| `SANITY_DEFAULT_AUTHOR_ID` | Sanity CMS (author doc `_id`) | Required |
| `SANITY_DEFAULT_AUTHOR_NAME` | Sanity CMS (author display name) | Required |
| `MINIMAX_API_KEY` | MiniMax LLM | Defined, not active — the provider is commented out in `custom_runner.py`'s `LLM_MODELS` list |
| `X_API_BEARER_TOKEN` | X/Twitter search | Defined, unused — `x_search_tool` isn't attached to any agent |
| `RAPID_API_YOUTUBE_TRANSCRIPT_API_KEY`, `YOUTUBE_TRANSCRIPT_IO_API_TOKEN`, `YOUTUBE_TRANSCRIPT_IO_API_TOKEN_2` | YouTube transcripts | Defined, unused — `tools/youtube_tools.py` isn't imported anywhere; the YouTube research agent is commented out |
| `ASSEMBLYAI_API_KEY`, `STARRYAI_API_KEY` | — | In the old README's `.env` template but referenced by **no code at all**. Skip these. |

The "defined, unused" rows exist in the code for a planned-but-not-wired-up
YouTube research feature — you don't need them to run the pipeline today. If
you're not planning to touch that feature, leave them unset.

## 1. FastAPI auth (`API_KEY`)

This just protects your own endpoints. Pick any string, set it as `API_KEY`,
then call the API with `Authorization: Bearer <that string>`. (Previously the
code ignored this and hardcoded `abc123` — that's fixed now; make sure whatever
you set here is what you actually use.)

## 2. Google Sheets — the pipeline's database

This is the most error-prone part of setup because the code expects **two
separate Google Sheets files** (not two tabs in one file), plus specific
worksheet names and column orders that aren't auto-created.

### 2a. Service account

1. Google Cloud Console → create/select a project → enable the **Google Sheets
   API** (and Google Drive API, since `gspread` resolves spreadsheets by title).
2. IAM & Admin → Service Accounts → create one → Keys → **Add key → JSON** →
   download it.
3. Set `GOOGLE_CREDENTIALS` to the **entire contents of that JSON file as a
   single-line string** (not a file path — the code does
   `json.loads(os.environ["GOOGLE_CREDENTIALS"])`).
4. Note the service account's email (looks like
   `xxx@your-project.iam.gserviceaccount.com`) — you'll share both sheets with
   it in the next step.

### 2b. Spreadsheet #1 — `ContentSpark_Keywords`

A standalone Google Sheet literally named `ContentSpark_Keywords`
(`tools/sheet_tool.py`'s `get_keyword_tool` calls
`client.open("ContentSpark_Keywords").sheet1` — the *first* sheet in that file,
not a named tab).

Columns (row 1 = header):

| A: `Keyword` | B: `Status` |
|---|---|
| your keyword or a YouTube URL | `available` |

The Triage Agent picks the first row with `Status = available` and flips it to
`used`. Add new keywords with `Status = available` whenever the queue runs low.

### 2c. Spreadsheet #2 — `ContentSpark`

A second Google Sheet named exactly `ContentSpark`, with these worksheets
(tabs) — create each with a header row matching the columns below:

| Worksheet | Columns (in order) | Written by | Read by |
|---|---|---|---|
| `research_data` | `Keyword/Topic, Search Volume, Difficulty, User Intent, Content Summary, Source URLs, Source Titles, Generated` | Output Agent (`/research`) | Brief Agent |
| `content_briefs` | `Keyword/Topic, Brief Content, FAQs, External Source Links, Content Summary, Generated` | Brief Agent (`/generate_brief`) | Content Generator Agent |
| `generated_posts` | `Title, Generated Content, FAQs, Quality Score, Summary, Approve/Disapprove, Published` | Content Generator Agent (`/generate_content`) | you (manual approval), and indirectly `approved_unpublished` |
| `approved_unpublished` | same 7 columns as `generated_posts` | **you must create this — no code writes to it** | Preparation Agent (`/post_content`) |
| `published_posts` | `Keyword/Topic, Post URL, Error` | Posting Agent (`/post_content`) | — |

**The `approved_unpublished` worksheet is the one manual-setup step that's easy
to miss.** The posting workflow reads row 2 of it directly
(`action="get_row", row_index=2`) and assumes it's already filtered — it does
not filter `generated_posts` itself. Set it up as a live filtered view, e.g. in
cell A1:

```
=FILTER(generated_posts!A2:G, generated_posts!F2:F="Approved", generated_posts!G2:G="No")
```

(column F = `Approve/Disapprove`, column G = `Published`, matching the header
order above). Approving a post in `generated_posts` (setting `Approve/Disapprove`
to `Approved`, which is the default the agent writes) then makes it show up here
automatically for the next `/post_content` run.

### 2d. Share both spreadsheets

Open each of the two spreadsheets → Share → add the service account email from
step 2a as **Editor**. Without this, every Sheets call fails with a permissions
error regardless of how correct `GOOGLE_CREDENTIALS` is.

## 3. LLM providers

The custom fallback runner (`blog_agent/custom_runner.py`) rotates across
these on quota/error — you need all three for the fallback to actually mean
anything:

- **Gemini** — `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/).
  Uses the `-latest` model aliases (`gemini-flash-latest`,
  `gemini-flash-lite-latest`), not dated snapshots like `gemini-2.5-flash` —
  those started 404ing with "no longer available to new users" for some
  projects (an active, unresolved Google-side inconsistency, confirmed via
  their own developer forum, not an intentional deprecation). The `-latest`
  aliases always resolve to whatever Google currently recommends, so this
  class of surprise shouldn't recur for the Gemini provider specifically.
  `.github/workflows/check-models.yml` exists to re-verify this at any time.
- **Cohere** — `COHERE_API_KEY` from [dashboard.cohere.com](https://dashboard.cohere.com/).
  Note: Cohere's free trial key is explicitly barred from production/
  commercial use by their own terms — worth a deliberate decision before
  relying on it for an unattended, real content pipeline.
- **OpenRouter** — `OPENROUTER_API_KEY` from [openrouter.ai](https://openrouter.ai/).
  Used two ways: `openrouter/free` (an auto-router that always resolves to
  whatever's currently free, immune to any single free model being delisted)
  as a regular fallback tier, and `deepseek/deepseek-v4-flash` as a **paid,
  genuinely-last-resort** tier — only tried once every free option above it
  has failed or is unavailable, regardless of how reliable it turns out to
  be (see the comment on `LAST_RESORT_MODELS` in `custom_runner.py`). Needs
  a funded OpenRouter balance to actually work; the free tier above doesn't.
  DeepSeek V4 Flash is text-only (no vision) — fine, since no agent that
  goes through the fallback rotation needs vision (the one that does,
  `image_quality_evaluation_agent`, is only ever invoked as a tool with its
  own fixed Gemini model, never swapped by the fallback runner).

Daily quota assumptions baked into `custom_runner.py`'s `model_limits`: Gemini
50, Cohere 33, OpenRouter (free tier) 50 — these are approximate free-tier
limits and worth checking against your actual plan. The paid DeepSeek tier
has no real daily cap (pay-per-token, not quota-limited).

## 4. Research/search

- **Tavily** — `TAVILY_API_KEY` from [tavily.com](https://tavily.com/). Primary
  research tool (search/extract/crawl) for the Research and Brief agents.
- **SerpAPI** — `SERPAPI_KEY` from [serpapi.com](https://serpapi.com/). Used as
  `web_search_tool`, the fallback when Tavily fails.

## 5. Images

Two-tier fallback in `generate_image_tool`/`get_stock_image_tool`:

1. **Freepik** (`FREEPIC_API_KEY` — note the key name has no "k", it's not a
   typo in this doc) — primary AI image generation, from
   [freepik.com/api](https://www.freepik.com/api). Note: new accounts get a
   one-time 5 EUR trial credit, not an ongoing free tier — budget for this
   once that's spent.
2. **Pexels** (`PEXELS_API_KEY`) — stock photo fallback if AI generation
   fails, from [pexels.com/api](https://www.pexels.com/api/). Genuinely free
   and generous (200 req/hr, 20K/month).

Hugging Face was removed as a fallback here (previously `HF_TOKEN` +
`black-forest-labs/FLUX.1-dev`) — its free Inference API tier was cut down to
a small monthly credit allowance in 2026 and had become unreliable.

## 6. Sanity CMS

1. Create a project at [sanity.io](https://www.sanity.io/) and note the
   **Project ID**.
2. `SANITY_DATASET` — usually `production`.
3. API token: Project → API → Tokens → **Add API token** with **Editor**
   (write) permission → `SANITY_API_TOKEN`.
4. Create (or find) an `author` document in the Studio, note its `_id` →
   `SANITY_DEFAULT_AUTHOR_ID`; set `SANITY_DEFAULT_AUTHOR_NAME` to match.
5. Your schema needs, at minimum, a `post` document type with these fields
   (read directly off `lib/sanity_adapter.py`'s `post_blog()`): `title`,
   `summary`, `slug`, `author` (reference), `mainImage` (image + `alt`),
   `categories` (array of references to a `category` type), `content`
   (Portable Text block array), and `faqs` (array of `{question, answer}`
   objects) — `faqs` in particular is not part of Sanity's default blog
   schema, so make sure your Studio schema actually has it or posts will
   publish with an empty FAQ section.
6. `category` documents need a `slug` that matches `slugify(category_name)` —
   the adapter resolves categories by slug, not by title, and silently drops
   any category it can't resolve.

## Discord bot + GitHub Actions setup

The pipeline runs via `.github/workflows/pipeline.yml` (scheduled + manual
`workflow_dispatch`), and approvals/manual triggering go through the Discord
bot in `discord_bot/`, deployed to the existing Hetzner VPS via Dokploy.
`main.py`/HF Spaces are no longer the active trigger path. Steps, in order:

### 1. Set the pipeline secrets in GitHub

Repo → Settings → Secrets and variables → Actions → New repository secret.
Add every "Required" row from the [Quick reference](#quick-reference) table
above (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `COHERE_API_KEY`,
`TAVILY_API_KEY`, `SERPAPI_KEY`, `PEXELS_API_KEY`, `FREEPIC_API_KEY`,
`SANITY_*`, `GOOGLE_CREDENTIALS`), plus one new one:
`DISCORD_WEBHOOK_URL` (from step 3 below).

### 2. Create the Discord application + bot

1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application.
2. Bot tab → enable the **Message Content** privileged intent (needed to read
   the title back out of the approval-request message when a reaction comes
   in). Copy the bot token → `DISCORD_BOT_TOKEN`.
3. OAuth2 → URL Generator → scopes: `bot` and `applications.commands`;
   permissions: `Send Messages`, `Read Message History`, `Add Reactions`,
   `Use Slash Commands`. Open the generated URL and add the bot to your
   server.
4. In the channel you want approvals/commands to happen in, right-click →
   Copy Channel ID (enable Developer Mode in Discord settings if you don't
   see this) → `DISCORD_APPROVAL_CHANNEL_ID`.

### 3. Create the incoming webhook (for draft notifications)

In that same channel: Channel Settings → Integrations → Webhooks → New
Webhook → copy the URL → `DISCORD_WEBHOOK_URL` (set as a GitHub secret per
step 1 — this is what `scripts/run_stage.py` posts to after the `content`
stage).

### 4. Create the GitHub PAT (lets the bot trigger workflow runs)

[github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens)
→ Fine-grained token → scope it to just this repository → Repository
permissions → **Actions: Read and write**. This is what lets `/run` in
Discord call `workflow_dispatch`.

### 5. Deploy the bot on Dokploy

1. In Dokploy: New Application → **Provider: Docker** → image
   `ghcr.io/<you>/seo-blog-agent-discord-bot:latest` → **no domain, no port
   mapping** (it's a background worker holding a Discord gateway connection,
   not an HTTP service) → restart policy: always.
2. Set these in Dokploy's runtime env panel (not baked into the image):
   `DISCORD_BOT_TOKEN`, `GITHUB_PAT`, `GITHUB_REPO` (`owner/repo`),
   `GOOGLE_CREDENTIALS`, `DISCORD_APPROVAL_CHANNEL_ID`.
3. Create a scoped, expiring Dokploy API key (Dokploy → API keys) and note
   this application's ID (its detail page in the panel).
4. Back in GitHub, add `DOKPLOY_URL` (`https://deploy.yourdomain.com`),
   `DOKPLOY_API_KEY`, and `DOKPLOY_APP_ID` as repo secrets — these are what
   `.github/workflows/deploy-bot.yml` uses to tell Dokploy to pull and
   restart the image after each build. Update the hardcoded `IMAGE:` value at
   the top of that workflow file to your actual GHCR path first.
5. Push to `master` (touching anything under `discord_bot/`) to trigger the
   first build + deploy, or run the workflow manually from the Actions tab.

### 6. Verify end to end

```bash
# Manually fire one stage from the Actions tab (or via the bot's /run command
# once it's deployed) and watch it in the Actions log:
gh workflow run pipeline.yml -f stage=research
```

Run stages one at a time (`research` → `brief` → `content` → `post`) rather
than waiting for the full cron schedule, same reasoning as before — isolates
a stale connection to one stage instead of a failure somewhere in the middle.
After `content` runs, confirm the Discord message shows up in the approval
channel, react ✅, and confirm the `Approve/Disapprove` cell in
`generated_posts` actually updates before trusting the `post` stage.

## Corrections to README.md

The original README's `.env` template listed a few things that don't match the
actual code:

- `OPENAI_API_KEY` — not required. No model in `LLM_MODELS` uses OpenAI, and
  tracing (the only thing that would have used it) is now explicitly disabled
  in `main.py`.
- `ASSEMBLYAI_API_KEY`, `STARRYAI_API_KEY` — referenced by no code anywhere in
  the repo (`assemblyai` is an unused `pyproject.toml` dependency; StarryAI was
  an originally-planned image provider that was never implemented — Freepik/HF
  are what's actually wired up).
- The "Google Sheets Setup" section described `ContentSpark_Keywords` as a
  worksheet inside the `ContentSpark` spreadsheet. It's actually a separate
  spreadsheet file — see [2b](#2b-spreadsheet-1--contentspark_keywords) above.
- It didn't mention `approved_unpublished` or `published_posts` at all, both
  of which the posting workflow requires.

README.md hasn't been rewritten — this doc is the accurate, code-verified
version; treat it as the source of truth for setup.
