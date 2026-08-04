# SEO & Content-Quality Audit — Internal (this repo)

Findings and fixes that live in **this** repository (`SEO-Blog-Agent`): agent prompts,
sheet-writing logic, and the pipeline's own architecture. For findings that require changes
on the live site (owaisabdullah.dev, a separate Next.js repo), see
`docs/seo_audit_external_frontend.md`.

## Methodology

Checked directly against the current source in this repo (`blog_agent/blog_agents.py`,
`blog_agent/research_agent.py`, `blog_agent/posting_agent.py`, `discord_bot/bot.py`) plus
live evidence pulled from owaisabdullah.dev (raw HTML/meta tags from 3 published posts, the
`/blog` listing page, `robots.txt`, `sitemap.xml`) to see what the current prompts actually
produce in practice, not just what they're supposed to produce. One external fact was
verified via Tavily search rather than relied on from training data, since it's dated and
recent: Google's FAQ rich-result policy.

## Executive summary

| # | Finding | Priority | Effort | Status |
|---|---|---|---|---|
| 1 | No character ceiling on generated blog post titles | High | Small | **Fixed** |
| 2 | Meta description isn't checked for AI-writing tells the way body content is | Medium | Small | **Fixed** |
| 3 | No deliberate topic-cluster/pillar strategy in topic selection | Medium | Medium | Documented, deferred |
| 4 | `published_posts` count is inflated — it logs failed/duplicate attempts, not just live posts | Medium | Small | **Fixed** (count only; sheet rows not cleaned up) |
| 5 | FAQ schema's SEO framing needs updating (Google fully deprecated FAQ rich results, May 2026) | Low (docs/mental-model only) | Small | N/A, informational |
| 6 | Posts publish with no categories ("Uncategorized" on the live site), and no reuse of existing categories | High | Small | **Fixed** |
| 7 | Title/Summary were never evaluated for actual click-worthiness (curiosity, value, intent match) | High | Small | **Fixed** |

## 1. No character ceiling on generated blog post titles — High priority

**What was found.** Live titles are consistently 77–95+ characters *before* the frontend adds
its own suffix. Sampled 3 published posts directly:

| Post | Title (as generated) | Length |
|---|---|---|
| Digital FTE | "Understanding the Digital FTE and How Smart Virtual Workers Handle End-to-End Tasks" | 85 chars |
| DeepSeek | "How to Use DeepSeek V4 Flash 0731 and V4 Pro for Free in Your Coding Agents" | 77 chars |
| Nano Banana Pro | "Gemini 3 Pro Image Gen (Nano Banana Pro): The Future of AI Image Generation" | 77 chars |

**Root cause.** `blog_agent/blog_agents.py` line 196, `content_generator_agent`'s Title/H1
instruction:

> "Include the primary keyword and make the title engaging and intent-driven. Create a
> compelling, curiosity-driven title that captures interest without being clickbait..."

This is entirely qualitative — no character or word limit anywhere. Compare directly against
line 197 (the very next instruction, for Summary): *"a short, SEO-friendly summary
(50–160 characters)..."* — the Summary field has an explicit, enforced-in-instructions
length ceiling; the Title field, which matters just as much for SERP display, has none.

**Why it matters.** Google truncates `<title>` display around ~50–60 characters (practically,
~580px of rendered width) — titles well past that get cut off mid-word in search results,
which both looks unpolished and can drop the primary keyword out of the visible portion if it
sits late in the title. This compounds with a frontend-side issue (a ~39-char site-name
suffix appended to every title — see the external doc, finding #1) to push every single post's
`<title>` tag to 116–130+ characters live.

**Fix applied.** Added an explicit "Keep it to 50-60 characters" ceiling to
`content_generator_agent`'s Title/H1 instruction (`blog_agent/blog_agents.py`, the "Title
(H1)" bullet), with the reasoning (site-suffix headroom, Google's ~580px truncation) stated
inline so the model has the "why," not just the number — mirrors the Summary field's existing
pattern.

**Still outstanding (deferred, not done in this pass).** This constraint is enforced via LLM
instruction text only, same as Summary's 50–160 char rule — not a deterministic Python check
like the row-shape validation this session added for other fields (see
`scripts/run_stage.py::_row_looks_malformed`). If titles keep coming in long despite the new
instruction, a soft deterministic warning in `_ensure_content_persisted` (flagging, not
truncating, an over-length Title before it reaches Discord for approval) would be the next
step — not implemented here.

## 2. Meta description isn't checked for AI-writing tells — Medium priority

**What was found.** `content_evaluation_agent`'s anti-AI-pattern checklist (blog_agents.py
line 21) explicitly flags things like inflated-significance phrases, vague attributions, and
signposting ("let's dive in") — but as part of the **readability score**, which is computed
against `HighestScoredContent` (body text) only. The Summary/meta-description field is never
run through this same evaluation loop.

Confirmed live: the published "Tavily: The Web Access Layer for AI Agents" post's meta
description reads:

> "In today's fast-paced world of artificial intelligence, giving AI agents real-time,
> reliable web access is incredibly important."

"In today's fast-paced world of..." is *exactly* the pattern `content_generator_agent`'s own
separate style guidance (line 214) explicitly bans: *"Avoid AI-generated sounding phrases
like 'In today's digital landscape'..."* — the instruction exists, it's just scoped to the
body, not the field that's most visible in search results (the meta description is the actual
SERP snippet text a user reads before clicking).

**Why it matters.** The meta description is often the first (and sometimes only) piece of
this site's actual writing a prospective visitor reads before deciding to click. A generic
AI-tell opener there undermines the "written by a real, credible person" impression the rest
of the pipeline works hard to establish (first-person voice, live author bio, banned-words
list).

**Fix applied.** `content_generator_agent`'s Summary instruction now explicitly states the
field "must follow the same Anti-AI-Pattern Checklist in section 4 below as the body
content," naming the exact failure mode found live ("In today's fast-paced world of...") as
the example to avoid, rather than leaving the checklist's scope ambiguous. Kept as a
reference to the existing checklist rather than duplicating it, so there's still only one
source of truth for the actual list of banned patterns.

## 3. No deliberate topic-cluster / pillar strategy — Medium priority (architecture)

**What was found.** Cross-referencing the live sitemap's 23 published post titles against
`blog_agent/research_agent.py`'s topic sourcing (the reactive Reddit/Quora trending-topic
discovery workflow, plus the ad hoc manual-keyword-entry path via the Triage Agent/Discord
`/add_topic`): topics are a mix of

- AI product/model news reviews ("Gemini 3 Pro Image Gen...", "DeepSeek V4 Flash 0731...",
  "MiniMax M2 vs Claude, GPT-5..."), competing directly against major tech outlets (TechCrunch,
  The Verge, etc.) with no differentiated first-hand expertise angle, and
- a smaller set of posts genuinely well-aligned with the site owner's actual professional
  identity (Digital FTE, spec-driven development, AI agent architecture) per the author bio
  fetched live via `get_author_context_tool`.

Neither `discover_topics`' Topic Discovery Agent prompt nor the Triage Agent's keyword
selection logic has any concept of a deliberate content cluster, pillar page, or topical
silo — every topic is evaluated independently ("is this trending right now / does someone
want it") with no check against "does this reinforce or dilute the site's core topical
authority."

**Why it matters.** This ties directly to the external doc's finding #3 (no real category
hub pages exist on the frontend either) — even if hub pages existed, there's currently
nothing upstream in *this* pipeline ensuring enough related posts get written on a given
pillar topic to make a hub page worth having. Google and AI answer engines both weight
demonstrated topical depth (a cluster of related, cross-linked posts) more than isolated
one-off posts on unrelated trending news.

**Recommended fix.** Not a quick prompt tweak — this is a genuine strategy decision for the
user to make (what 3-5 pillar topics does this blog want topical authority in?), but once
decided, two concrete pipeline changes would operationalize it:
- Add a small, explicit list of target pillar topics/categories to `discover_topics`'
  Topic Discovery Agent prompt and to the Triage Agent's selection logic, weighting
  candidates that reinforce an existing pillar over one-off trending news.
- `fetch_internal_links_tool` (used by `content_generator_agent`) already does semantic
  internal-link matching — worth checking whether it's actually surfacing same-cluster posts
  preferentially, or just whatever's topically closest regardless of cluster membership.

## 4. `published_posts` sheet count is inflated — Medium priority, confirmed root cause

**What was found / confirmed.** The live `sitemap.xml` lists 23 blog post URLs. Earlier this
session, the Discord bot's `/status` reported "Total published: 37" — a 14-post gap. The site
owner confirmed directly: `published_posts` also logs posts whose publish attempt errored, and
duplicate-attempt rows from the (now-fixed) duplicate-Sanity-publish bug. That matches the
prompt-level root cause found in `blog_agent/posting_agent.py` lines 201–205 (Posting Agent's
own instructions, step 5):

> "Record in published_posts worksheet: Add a new row to 'published_posts' worksheet with:
> Keyword/Topic, Post URL, Error: empty string **if successful**"

The phrasing "empty string if successful" implies a row gets added on *every* posting
attempt — success or failure — with the Error column populated on failure rather than the
row being skipped. Combined with the several real duplicate-Sanity-publish bugs this session
found and fixed (see `docs/fixes_and_improvements.md`) — where a single logical post could
trigger `post_to_sanity_tool` more than once due to retry/race conditions — each of those
duplicate attempts would also have logged its own `published_posts` row before those bugs
were fixed.

Separately, `discord_bot/bot.py`'s `gather_pipeline_status()` (the source of the `/status`
"Total published" number) reads `published_posts` with zero filtering:

```python
published_records = _get_content_spark_worksheet("published_posts").get_all_records()
status["posts_published_total"] = len(published_records)
```

This counts every row unconditionally, including ones with a populated Error value and any
leftover duplicate-attempt rows from before this session's dedup fix (deterministic Sanity
`_id` + `createIfNotExists`, see `lib/sanity_adapter.py`).

**Fix applied.** `discord_bot/bot.py::gather_pipeline_status` now filters `published_records`
to rows where the Error column is empty before counting, so "Total published" reflects
actually-successful publishes, not attempts.

**Still outstanding (deferred, not done in this pass).** A one-time cleanup to reconcile
`published_posts` against the live Sanity document count (or the sitemap) and remove/mark
stale error/duplicate-attempt rows was proposed but explicitly deferred — the code fix above
stops the count from being *newly* wrong going forward, but old rows with a populated Error
value still sit in the sheet and would need a manual or scripted cleanup pass if the sheet
itself (not just the derived count) needs to be tidy.

## 5. FAQ schema's SEO value has changed — Low priority, mental-model update only

**What was found.** Verified via Tavily (dated, not training-data-guesswork): Google fully
deprecated FAQ rich results in Search as of **May 7, 2026** — not the 2023 restriction to
government/health sites, the entire visual SERP feature no longer renders for *anyone*, with
Search Console/Rich Results Test support winding down through August 2026. Every post this
pipeline generates still includes a `FAQPage` schema block (via `post_to_sanity_tool`'s
`faqs` field), and that's still the right call — sources agree FAQ markup continues to help
AI answer engines (ChatGPT, Perplexity, Google AI Overviews/AI Mode) extract and cite
Q&A-shaped content, and helps Google's own crawler parse the page even without a visual
reward.

**Why it matters.** Nothing in this repo currently needs to change *code-wise* — this is
purely a note so future decisions about the FAQ-generation step (in `brief_agent` and
`content_generator_agent`) aren't made on the outdated assumption that it's earning a Google
rich-snippet. It's GEO/AI-citation infrastructure now, not classic-SERP infrastructure.

**Recommended fix.** None required. Documented here so it's part of the record.

## 6. Posts publish with no categories, and no reuse of existing ones — High priority

**What was found.** Confirmed via `resolve_categories_to_refs` in `lib/sanity_adapter.py`:
categories are only ever *matched* against pre-existing Sanity `category` documents by
slugified name — if no document with that exact slug already exists, the code just logs
"Category ... not found ... Skipping reference" and moves on, leaving the post with zero
categories. That surfaces on the live site as "Uncategorized."

The actual place `CATEGORIES` gets invented is the **Preparation Agent**
(`blog_agent/posting_agent.py`, step 5, at publish time) — not `content_generator_agent` as
initially assumed while drafting this doc; corrected here. Its instruction was just "Derive
from Keyword/Topic," with no visibility into what categories already exist and no memory
across runs — one publish invented `["AI", "Automation", "Digital FTE"]` for a topic, a retry
of the *same* post invented `["AI Agents", "Automation", "Digital FTE"]`. Since matching
required an exact slug hit, and every run was independently free-associating category names,
almost every post's first attempt at a given category name would fail to match anything and
get silently dropped.

**Why it matters.** Two compounding problems, not one: (1) posts frequently end up with no
categories at all, and (2) even when categories do resolve, a proliferating set of
near-duplicate categories ("AI" / "AI Agents" / "AI Tools" / "Artificial Intelligence") is
functionally the same as having no real taxonomy — it defeats the entire point of
categorization (topical grouping, the same theme as finding #3 and the external doc's
finding #3 on missing category hub pages).

**Fix applied, two parts:**
- `SanityAdapter.resolve_categories_to_refs` now auto-creates a missing category document
  (via the same `createIfNotExists` idempotent pattern already used for authors and posts,
  keyed on a deterministic `category-{slug}` id) instead of silently skipping it — a post can
  no longer end up Uncategorized just because it's the first time a category name was used.
- New `SanityAdapter.list_categories()` + a new `get_existing_categories_tool` (`tools/tools.py`),
  wired into the Preparation Agent's tool list. Its CATEGORIES instruction now requires calling
  this tool first and explicitly preferring reuse of an existing category name over inventing a
  near-duplicate, only proposing a genuinely new one when nothing existing reasonably fits.

**Caveat.** Category document creation assumes the Sanity schema's category type uses a
`title` field (a near-universal convention, matching the existing `slug` field the original
query code already expected) — if the live schema actually uses a different field name (e.g.
`name`), newly-created categories would still resolve/reference correctly (matching is by
slug) but might not display a label in Sanity Studio. Worth a quick manual check in Studio
after the next publish.

## 7. Title/Summary were never evaluated for actual click-worthiness — High priority

**What was found.** `content_evaluation_agent` had a "Title Guidance (Evaluation)" section
that was effectively vestigial: Title wasn't listed in the formal Inputs section at all
(meaning whether it was actually included in a given `get_evaluation_feedback` call depended
on the calling model's own judgment, not an explicit instruction), and Summary/meta
description wasn't evaluated for anything beyond length — not curiosity, not clarity of
value, not intent match, not the same anti-AI-tell checklist the body gets (see finding #2).
Neither of the two scoring formulas in play (`content_evaluation_agent`'s own 0.4/0.4/0.2, or
`content_generator_agent`'s restated 0.3/0.3/0.2/0.2) had an explicit scored component for
"would a searcher actually click this."

**Why it matters.** This is the direct SEO/GEO consequence of the previous findings: a title
that's technically within the character ceiling (finding #1) and a summary free of AI-tells
(finding #2) can still both be functionally generic labels ("Understanding X," "A Guide to
X") that a searcher has no particular reason to click over a competing result. Length and
tone constraints are necessary but not sufficient — nothing was actually checking whether the
snippet *works* as a piece of persuasive, intent-matched copy.

**Fix applied.** Expanded `content_evaluation_agent`'s guidance into an explicit "Title &
Meta Description Guidance (Evaluation)" section, now scoring Title and Summary together
against: curiosity/hook strength, clear value proposition, intent match (informational vs.
commercial vs. transactional), accuracy/no-overpromise, and absence of AI-tell openers in the
Summary specifically. This is now a real, scored gate, not just descriptive guidance: a
generic/non-curious title or summary caps the SEO sub-score at Medium regardless of how well
the rest of the SEO checklist (word count, keywords, FAQs, links) is satisfied, which forces
another iteration rather than letting a technically-complete-but-unclickable snippet through.
Also made Title and Summary formal Inputs (no longer implicit), and updated
`content_generator_agent`'s own iteration step to (a) always include Title/Summary in each
`get_evaluation_feedback` call, and (b) explicitly revise Title/Summary based on feedback
during iteration, not just the body -- and to track them as a matched set across iterations
so a revised body never ends up paired with a stale title.
