# Architecture Roadmap — Tools & Third-Party Services

Forward-looking, not a bug list (see `docs/seo_audit_internal_pipeline.md` /
`docs/seo_audit_external_frontend.md` for those). This covers what third-party
tools/services/models could meaningfully improve the pipeline — image generation, keyword/
SEO research, and performance visibility — based on current (August 2026) pricing and
availability, verified via Tavily rather than relied on from training data given how fast
this specific market moves.

**Nothing here is implemented yet.** This is a menu with recommendations, not a plan already
in motion — flagged explicitly since the previous doc in this session (the SEO audit) did
include applied fixes, and this one deliberately doesn't.

## A note on how fast this space moves (read before trusting any number below)

Researching this surfaced a concrete, well-documented example of exactly the volatility to
expect: on December 7, 2025, Google cut Gemini API free-tier request limits by 50–92%
overnight, with no advance warning (Gemini 2.5 Flash went from ~250 requests/day to 20/day) —
this is the same cut this session's own live logs already hit directly (`gemini-flash-latest`
capped at 20 RPD in production, confirmed in `docs/fixes_and_improvements.md`'s per-model
quota work). Multiple sources researched for this doc disagree with each other on current
free-tier numbers for the same model, because they were written at different points before/
after that cut. **Treat every specific quota/price number below as "true as of this
research," not "true when you read this" — check the provider's own current pricing/console
page before committing engineering time to any of these.**

## 1. Image generation

**Current state.** Cloudflare Workers AI (`@cf/black-forest-labs/flux-2-dev`) is the sole AI
generator (genuinely free, 10,000 Neurons/day), falling back to Pexels stock photos. Freepik
was removed earlier this session (expired key, one-time trial credit only, not an ongoing
free tier).

**Option 1 — Gemini "Nano Banana" family, via the `GEMINI_API_KEY` already configured.**
This is the most immediately actionable option since it needs zero new signup/key — the
pipeline already authenticates to the Gemini API for every text agent.
- `gemini-2.5-flash-image` ("Nano Banana") and the newer `gemini-3.1-flash-image` ("Nano
  Banana 2") are Google's native image models — strong prompt adherence and, notably,
  strong in-image text rendering (useful for diagram-style or text-in-image hero images Flux
  tends to mangle).
- `gemini-3-pro-image-preview` ("Nano Banana Pro") is the higher-quality tier: up to 4K,
  ~94% text-rendering accuracy, multi-image reference/character consistency. Confirmed **no
  free API tier** for this one specifically — $0.134/image (1K–2K), $0.24/image (4K), half
  price via Batch API. Free access exists only through the consumer Gemini app (~2–3
  Pro-quality images/day) or limited AI Studio testing quota, not suitable for pipeline
  automation.
- The regular (non-Pro) tier's free-tier situation is genuinely unclear from current sources
  — some claim up to 500 free requests/day via the Developer API, but that figure doesn't
  account for the same Dec-2025-style cut that hit the text models, and no source could
  confirm a current, stable number. **Verify directly in Google AI Studio's console before
  relying on it**, exactly like this session already had to do for the text-model quotas.

**Option 2 — GPT Image 2, via the `OPENROUTER_API_KEY` already configured.** OpenAI's
current image model (released April 21, 2026), reachable through OpenRouter
(`openai/gpt-image-2`) without a separate OpenAI account/key. No free tier — roughly
$0.03/$0.05/$0.06 per image at 1K/2K/4K. Strong prompt adherence and multilingual text
rendering.

**Option 3 — kie.ai (user-suggested), a pay-as-you-go aggregator for Nano Banana/Nano Banana
Pro/Flux/GPT-image behind one API key.** Verified via Tavily across several independent
sources: credit-based, no subscription, $5 minimum top-up, and consistently *cheaper than the
official vendor price* on every model checked -- Nano Banana Pro at ~$0.09–0.12/image versus
Google's own $0.134–0.24 (a 20–40% discount), Nano Banana (non-Pro) at ~$0.02/image, plus
Flux Kontext and GPT-image access through the same account. This sidesteps the single biggest
risk in Option 1 entirely: there's no ambiguous, provider-controlled "free tier" that can get
cut 92% overnight the way this session already watched happen to Gemini's text models --
it's a stable, predictable, low per-image cost from the start. The tradeoff is that it's a
third-party reseller sitting between this pipeline and Google/OpenAI's actual infrastructure,
not the vendor directly -- worth a reliability/ToS check before depending on it in production,
though it appears to be an established service (one source cited 32,740+ ratings).

**Recommendation.** Given this session's repeated, direct experience with Gemini's own free
tier being unpredictable (the per-model quota-tracking work earlier in this session exists
*because of* exactly this instability), a small, stable, pay-as-you-go cost via kie.ai is
plausibly the better fit here than chasing another free tier with unknown current limits --
predictable is worth more than free-but-volatile for something running on an unattended
schedule. Suggested path: add kie.ai as a paid tier *below* Cloudflare (still try the genuinely
free 10,000 Neurons/day first), giving `image_selection_agent` a cheap, stable paid option
before ever falling all the way back to generic Pexels stock photos -- better image quality
for a few cents, only spent on the days Cloudflare's free quota is already exhausted. Leave
GPT Image 2 direct and raw Gemini Nano Banana as documented, not-yet-wired alternatives rather
than implementing either now.

## 2. Keyword & SEO research

**Current state.** This is the more significant gap. Topic selection currently has **no real
search-demand data feeding it at all** — the Triage Agent picks the next manually-queued
keyword, and `discover_topics` proposes candidates purely from what's trending on Reddit/
Quora right now (via Tavily search). Nothing checks whether a candidate keyword is actually
searched for, how competitive it is, or what its real intent breakdown looks like before it
gets queued. This directly compounds the internal audit's finding #3 (no pillar/cluster
strategy) and #6 (category/taxonomy gaps) — right now there's no data-driven signal at any
stage of "is this topic actually worth writing about" beyond "is it currently being discussed
somewhere."

**Option 1 — DataForSEO (pay-per-call API).** Already referenced by the `seo-dataforseo`
skill available in this environment. Programmatic, cheap relative to Ahrefs/Semrush
($108–140/mo subscriptions), and — unlike Google Keyword Planner — designed for API/pipeline
integration rather than a human clicking through a UI. Would let `discover_topics`' Topic
Discovery Agent and/or the Triage Agent score candidates by real search volume + difficulty
before queueing them, not just trending-ness.

**Option 2 — Google Keyword Planner (free, via a Google Ads account, no ad spend required).**
The most trustworthy volume/seasonality data source, zero cost — but it's a UI-first tool,
not built for clean programmatic access the way this pipeline's other tools are (Tavily,
Sanity, Sheets). Best used as a free supplement for spot-checking, not as the primary
automated signal.

**Option 3 — AnswerThePublic / Ubersuggest free tiers.** Good for question-style/intent
ideation (AnswerThePublic specifically), very limited free quotas (3–5 queries/day) — useful
as occasional manual research, not something to wire into an automated daily pipeline.

**Recommendation.** DataForSEO is the right fit architecturally (API-first, matches how every
other tool in this pipeline works) and cheap enough to be worth it even for a small-scale
blog. Concretely: add a `check_keyword_demand_tool` (DataForSEO's search-volume/difficulty
endpoints) and wire it into `discover_topics`' Topic Discovery Agent, scoring/filtering
candidates by real demand before they're ever posted to Discord for approval — right now a
human reviewer has no data to approve/reject a candidate against, just the "why now" trending
rationale.

## 3. Search performance visibility (new capability, not a replacement)

**Current state.** The pipeline has zero visibility into how published posts actually
perform in search — no impressions, clicks, average position, or query data comes back
anywhere in this system after a post goes live.

**Recommendation — Google Search Console API.** The site is already verified with Google
(confirmed live: `<meta name="google-site-verification" content="92FJDtkgr_..."/>` present on
every page during this session's SEO audit), so there's no new verification step needed —
just OAuth/service-account setup for the Search Console API, which is entirely free. This
would close the loop the SEO audit doc's findings can only currently guess at: e.g., finding
#1 (title truncation) could be confirmed or refuted with real CTR data instead of inferred
from character counts, and finding #3/#6 (topic clustering) could be validated against which
pillar topics are actually driving impressions. Also the only reliable way to eventually
check whether `sitemap.xml` posts are actually being indexed (coverage reports), which no
other tool in this stack can tell you.

## 4. Other pipeline feature ideas (not third-party services)

Broader than tools/APIs — features that fit naturally into the *existing* architecture
(sheets, Sanity, Discord bot, the agent chain) without necessarily needing a new external
service.

**Content freshness sweep.** Nothing currently revisits an old post after it's published.
The internal audit doc's finding #5 (FAQ schema) already flagged that AI/tool-review content
specifically goes stale fast — proven directly during *this session's own research* for this
doc: pricing figures for Nano Banana Pro, GPT Image 2, etc. varied across sources by month
because the underlying prices kept changing. A post like "How to Use DeepSeek V4 Flash 0731...
for Free" will have wrong pricing/availability details within weeks. Concretely: a scheduled
stage (e.g. monthly) that lists posts older than N months, has an agent fact-check specific
claims via `tavily_search_tool` against the live web, and if something's materially changed,
uses the **already-built `edit_post` stage** (this session) to patch just the outdated part —
this is now a small addition, not new infrastructure, since edit_post already does exactly
"targeted change, preserve everything else" and already patches the live Sanity doc.
Also directly feeds `dateModified` in `BlogPosting` schema (currently static at publish time
per the external audit doc's finding #2), a real freshness signal for both Google and AI
answer engines.

**Duplicate/near-duplicate topic detection before queueing.** Nothing currently checks
whether a proposed research/`discover_topics` candidate is substantially the same as an
already-published or already-queued topic. Given `discover_topics` pulls from live
Reddit/Quora trending discussion every ~2 days, the same underlying story ("new Gemini
checkpoint," "new DeepSeek release") can plausibly resurface and get queued twice, wasting a
full research→brief→content cycle on content that already exists. Concretely: before adding a
candidate to `ContentSpark_Keywords`, semantic-compare it (even a cheap Gemini call, or
`fetch_internal_links_tool`'s existing semantic matching repurposed for this) against titles
already in `generated_posts`/`published_posts`, and skip/flag near-duplicates instead of
queueing them blind.

**Revive the planned Repurposing Agent.** Worth noting explicitly: this isn't a new idea, it's
already the project's own stated (but never built) design — `README.md` and
`docs/overview.md` both describe a "Publisher and Repurposer Agent" meant to reformat
published posts for Reddit/Quora/LinkedIn/YouTube and post them with backlinks, writing to a
`repurposed_content` worksheet that doesn't exist yet. This closes a real loop: topic
discovery already *pulls* from Reddit/Quora trending discussion — repurposing would let the
pipeline *contribute back* to those same communities (with a backlink), which is both direct
traffic and an authoritativeness/backlink signal the SEO audit doc's E-E-A-T section flagged
as currently thin. Realistic v1 scope: a `repurpose` stage that drafts a LinkedIn-style post +
a Reddit-comment-style summary for Discord approval (like drafts already work), holding off on
direct platform API posting (LinkedIn/Reddit API access is a meaningfully bigger scope
increase) until the drafting half proves useful.

**Search-performance-driven improvement loop** (depends on Search Console, section 3 above).
Once real impression/CTR data exists, a scheduled stage could flag posts with high impressions
but low CTR (a title/meta problem, not a content problem) or high impressions but low average
position (a content-depth/authority problem) and propose them as `edit_post` candidates via
Discord for approval — turning the new title/summary CTR-appeal evaluation gate (this
session's fix) from a one-time publish-time check into an ongoing, data-driven one.

**Weekly/monthly digest to Discord.** Low effort, uses infrastructure that already exists —
`gather_pipeline_status()` (`discord_bot/bot.py`) already computes most of what a digest
needs. A scheduled GitHub Action posting a weekly summary (posts published, average quality
score, topics researched, any stages that failed) would give a passive pulse-check without
needing to run `/status` manually, and if the freshness-sweep and topic-dedup ideas above ever
ship, would be the natural place to also report "N posts flagged for refresh" / "N duplicate
topics skipped."

**Comparison-table / structured comparison generator for tool-review posts — IMPLEMENTED,
correction from initial framing.** A meaningful share of current content is "X vs Y" or
tool-review style (confirmed in the internal audit's finding #3/#6 — Gemini checkpoints,
DeepSeek releases, MiniMax comparisons). These formats benefit disproportionately from actual
structured comparison data, which the skill's own GEO guidance calls out specifically
("tables and lists for comparative data" as an AI-citation signal).

Initial framing here assumed a literal Markdown pipe-table would just work -- checked against
`lib/markdown_parser.py` before implementing and that's false: it uses the base `commonmark`
Python library with no GFM table extension, so `| Column | Column |` syntax isn't recognized
as a table at all and would render as broken, garbled paragraph text on the live site (pipe
and dash characters shown literally). `content_generator_agent`'s instructions now require a
clearly-labeled bulleted/numbered structured breakdown for comparison posts instead (which the
parser handles fine), and explicitly forbid pipe-table syntax. Real Markdown table support
could still be added to `lib/markdown_parser.py` later (parsing GFM pipe-table syntax into
Sanity blocks) if a genuine HTML `<table>` is wanted -- that would also require confirming the
Sanity schema/frontend actually has a `table` Portable Text block type to render into, which
wasn't verified here (no frontend repo access). Flagged as a possible follow-up, not done.

### Pipeline-feature priority (separate from the paid-service table below, since none of these need a new external account)

| Idea | Depends on | Effort | Priority | Status |
|---|---|---|---|---|
| Comparison-table generator for review posts | Nothing new | Small (prompt-only) | High | **Implemented** (as structured bullets, not literal tables -- see correction above) |
| Duplicate/near-duplicate topic detection | Nothing new | Small–Medium | High | **Implemented** -- `_check_duplicate_topic` in `discord_bot/bot.py`, advisory (warns, never blocks), wired into both the topic-candidate reaction flow and `/add_topic` |
| Weekly Discord digest | Nothing new | Small | Medium | **Implemented** -- `weekly_digest` task loop in `discord_bot/bot.py`, posts generated/briefed counts + avg quality score + stage failures over the last 7 days |
| Content freshness sweep | `edit_post` (already built) | Medium | Medium | **Implemented, real rotation** -- `freshness_sweep` stage, weekly schedule, detect-and-suggest only (never auto-applies). Fixed a real bug caught live: age now comes from Sanity's own `_createdAt` (`SanityAdapter.list_posts`), not the pipeline-side "Created At" sheet column -- that column only exists for posts created after this session, so it was silently excluding every pre-existing (including year-old) post forever. Also added a "Last Freshness Check" rotation column so it actually cycles through the whole catalog instead of re-picking the same post every run indefinitely. Every outcome (including "nothing eligible") now posts an explicit Discord message instead of the bare generic ping. |
| Search-performance-driven edit loop | Search Console API (section 3) | Medium | Medium | **Implemented, Sanity-driven** -- access confirmed live. `search_performance_review` checks striking-distance queries first (real search terms already getting impressions at position 5-15, naming the exact phrase to work into the content), falling back to the blunt low-CTR/poor-position page-level heuristics only when no such query exists. Country/device breakdown attached as reviewer context. Candidate selection comes directly from Sanity's live post list (not the `generated_posts` sheet) -- confirmed live that some published posts have no sheet row at all (pre-existing/pre-pipeline posts), which a sheet-driven selection could never see regardless of the age source. "Last Performance Check" rotation column persists best-effort when a matching sheet row exists. |
| Repurposing Agent (drafts only, no auto-posting) | Nothing new | Medium–Large | Low–Medium | **Implemented, recommend-then-pick** -- per explicit request, the scheduled `repurpose` run only *recommends* not-yet-repurposed posts (title, URL, and a one-line suggested angle per candidate) and drafts nothing on its own. The user names a specific post (via `/repurpose title:X` or chat) to actually get LinkedIn + Reddit copy drafted for it. Voice/structure follows the `social-media-writer` skill (banned-word list, LinkedIn length/format rules, no-hype tone). Also now generates a tailored AI image prompt alongside the two copy drafts, saved to `repurposed_content`. |

## Priority summary

| Item | Cost | Effort | Priority | Status |
|---|---|---|---|---|
| Google Search Console API integration | Free | Small–Medium | High | **Implemented** -- see the pipeline-feature table above; access verified live via a real query returning actual clicks/impressions |
| kie.ai as a paid image fallback below Cloudflare | ~$0.02–0.12/image, pay-as-you-go, $5 min top-up | Small | Medium–High | Not started — cheap, stable, sidesteps the free-tier-volatility risk entirely |
| DataForSEO for topic/keyword demand scoring | Low (pay-per-call) | Medium | Medium–High | Not started — the most architecturally significant gap found, but first paid tool this pipeline would adopt |
| Gemini Nano Banana as a second free image source | Free (pending live quota verification) | Small | Low–Medium | Not started — same resilience benefit as kie.ai, but with the same free-tier-cut risk as the text models |
| GPT Image 2 direct (no aggregator) | Pay-per-image, no free tier | Small | Low | Not started — kie.ai reaches the same/similar models cheaper |

## Open questions for the user before any of this gets implemented

- ~~Search Console: who has admin access...~~ Resolved -- access granted and verified live
  (see `docs/service_setup.md` section 7, `scripts/test_search_console.py`).
- kie.ai / DataForSEO (or any paid tool): what's an acceptable monthly budget, given every
  other service in this pipeline was deliberately chosen to be free? kie.ai's per-image cost
  is small, but it's still the first ongoing per-use cost this pipeline would take on.
- kie.ai specifically: comfortable depending on a third-party reseller sitting between this
  pipeline and Google/OpenAI, rather than a direct vendor relationship? Worth a quick look at
  their ToS/reliability track record before wiring in production traffic.
- Nano Banana (direct): worth a quick live check of the actual current free-tier RPD for
  `gemini-2.5-flash-image` in Google AI Studio before committing to wiring it in, given the
  conflicting numbers found during this research -- though given kie.ai's stability advantage,
  this may not be worth pursuing at all.
