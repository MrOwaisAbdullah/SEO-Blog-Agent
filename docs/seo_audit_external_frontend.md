# SEO & Content-Quality Audit — External (owaisabdullah.dev frontend)

Findings that require changes on the **live Next.js site** (owaisabdullah.dev), which is a
separate repository from this one (`SEO-Blog-Agent`, the content-generation pipeline). None
of these can be fixed here — this doc exists to hand off precisely enough that whoever/
whatever works on the frontend repo doesn't need to re-derive the evidence. For pipeline-side
findings (agent prompts, sheet logic), see `docs/seo_audit_internal_pipeline.md` in this repo.

## Methodology

All findings below are from live evidence: raw HTML/`<head>` pulled directly via `curl` (not
a JS-rendered browser, so this reflects exactly what a search crawler/scraper sees) from 4
URLs — 3 published blog posts and the `/blog` listing page — plus `robots.txt` and
`sitemap.xml`. One external fact (Google's FAQ rich-result policy) was verified via Tavily
search rather than relied on from training data, since it's dated and recent (May 2026).

## Executive summary

| # | Finding | Priority | Effort |
|---|---|---|---|
| 1 | `<title>` tags are 100–130+ characters (SERP truncation), plus a double-suffix bug on `/blog` | High | Small |
| 2 | `BlogPosting` JSON-LD author signal is thin (name only, no `url`/`sameAs`) | Medium | Small |
| 3 | Blog categories have no crawlable URL — client-side filter tabs only | Medium/High | Medium |
| 4 | `/blog` listing page has zero JSON-LD structured data | Low/Medium | Small |

## 1. `<title>` tags are systemically too long — High priority

**What was found.** Raw `<title>` tag, pulled via `curl` from 3 live posts:

```
Understanding the Digital FTE and How Smart Virtual Workers Handle End-to-End Tasks | Spec-Driven Developer & AI Engineer   (121 chars)
How to Use DeepSeek V4 Flash 0731 and V4 Pro for Free in Your Coding Agents | Spec-Driven Developer & AI Engineer            (116 chars)
Gemini 3 Pro Image Gen (Nano Banana Pro): The Future of AI Image Generation | Spec-Driven Developer & AI Engineer            (117 chars)
```

Every one follows the same pattern: `{post title} | Spec-Driven Developer & AI Engineer`. The
`/blog` listing page is worse — it double-stacks two suffixes:

```
Blog | Owais Abdullah - Spec-Driven Development & AI Insights | Spec-Driven Developer & AI Engineer   (100 chars)
```

Telling evidence this is a *global template* issue, not per-page: the `og:title` meta tag on
the same Digital FTE post uses a noticeably **shorter** suffix than the `<title>` tag does —

```html
<title>Understanding the Digital FTE and How Smart Virtual Workers Handle End-to-End Tasks | Spec-Driven Developer &amp; AI Engineer</title>
<meta property="og:title" content="Understanding the Digital FTE and How Smart Virtual Workers Handle End-to-End Tasks | Owais Abdullah"/>
```

`<title>` appends `" | Spec-Driven Developer & AI Engineer"` (39 chars); `og:title` appends
`" | Owais Abdullah"` (17 chars). Two different suffix templates exist in the codebase
already — one of them is just noticeably better.

**Why it matters.** Google truncates `<title>` display around ~50–60 characters (practically
~580px of rendered width). Titles this long get cut off mid-word in search results, and on
the `/blog` listing page the truncation would likely cut before the page's own actual
description even starts rendering.

**Recommended fix.**
- Use the shorter suffix pattern (`| Owais Abdullah`, already implemented for `og:title`) for
  the `<title>` tag too, for consistency and length.
- On `/blog`, remove the double-suffix — the page's own title
  ("Blog | Owais Abdullah - Spec-Driven Development & AI Insights") already carries branding;
  the global per-page suffix shouldn't stack on top of a title that already has one.
- Consider whether blog *post* pages need the site-name suffix in `<title>` at all — a common,
  Google-recommended pattern is to drop the brand suffix on article pages entirely (Google
  often synthesizes/shows the site name separately as a "site name" badge above the title in
  modern SERPs) and reserve it for the homepage/about/contact-style pages. This is the single
  biggest lever here since it doesn't depend on the pipeline-side title-length fix (see the
  internal doc, finding #1) landing first — either fix alone helps; both together are needed
  to get consistently under ~60 chars.

## 2. `BlogPosting` schema's author signal is thin — Medium priority

**What was found.** Raw JSON-LD from a live post:

```json
{"@context":"https://schema.org","@type":"BlogPosting",
 "headline":"Understanding the Digital FTE and How Smart Virtual Workers Handle End-to-End Tasks",
 "image":"https://cdn.sanity.io/images/...",
 "author":{"@type":"Person","name":"Owais Abdullah"},
 "publisher":{"@type":"Organization","name":"Owais Abdullah","logo":{"@type":"ImageObject","url":"https://owaisabdullah.dev/assets/logo.png"}},
 "datePublished":"2026-08-02T13:13:38Z",
 "dateModified":"2026-08-02T13:13:38Z",
 "description":"...",
 "mainEntityOfPage":{"@type":"WebPage","@id":"..."}}
```

The `author` object has `name` only — no `url` (a link to an author bio/about page) or
`sameAs` (an array of social/professional profile URLs — LinkedIn, GitHub, X, etc.), both of
which `Person` schema supports directly.

**Why it matters.** This is one of the cheapest, most direct Expertise/Authoritativeness
(E-E-A-T) signals available: `author.url` and `author.sameAs` are exactly what lets Google
(and AI answer engines building an entity graph) connect "the person who wrote this" to a
verifiable, credentialed identity elsewhere on the web, rather than just a bare name string.

**Recommended fix.** Add to the `author` object wherever `BlogPosting` schema is generated:
- `"url": "https://owaisabdullah.dev/about"` (or wherever the author bio page actually lives)
- `"sameAs": ["<LinkedIn URL>", "<GitHub URL>", ...]` — whichever professional profiles the
  site owner wants attributed.

This is static, site-wide data (not per-post), so it's a small, one-time change to wherever
the `BlogPosting` JSON-LD is constructed.

## 3. Blog categories have no crawlable URL — Medium/High priority

**What was found.** On `/blog`, category filtering is implemented as client-side Radix UI
tabs — confirmed directly in the raw HTML:

```html
<button type="button" role="tab" aria-selected="false" ... data-state="inactive"
  id="radix-..._trigger-SaaS">SaaS<span class="ml-2 text-xs opacity-70">(1)</span></button>
```

These are `<button>` elements with `data-state`/`aria-selected`, not `<a href="...">` links.
There is no `/blog/category/{slug}`-style URL anywhere in the page's HTML. Every category
(e.g. "Web", "SaaS", visible as tab labels with post counts) exists purely as an in-memory
JS filter over the same single `/blog` page — a search crawler sees one undifferentiated list
of all posts, never a topically-scoped subset.

Note this data already exists upstream: `CATEGORIES` is generated per-post by the pipeline
(`content_generator_agent`, this repo) and stored on the Sanity document via
`post_to_sanity_tool`. The data model supports real category pages; the frontend just doesn't
expose them as distinct routes.

**Why it matters.** This directly works against topical-authority signals both classic Google
ranking and AI/GEO citation systems reward — a real, indexable "AI Agents" or "SaaS
Architecture" hub page that lists and links every related post is a strong topical-cluster
signal; a client-side-only filter on one page is invisible to crawlers and contributes
nothing.

**Recommended fix.** Add real category archive routes (e.g. `/blog/category/[slug]`), each
server-rendered with its own `<title>`/meta description and a list of that category's posts,
linked from the existing tab UI (or replacing it). Each category page should ideally also
carry its own `CollectionPage`/`ItemList` JSON-LD (see finding #4 below, which currently
applies to zero pages on the blog side).

## 4. `/blog` listing page has zero structured data — Low/Medium priority

**What was found.** Every individual post has solid `BlogPosting` + `FAQPage` +
`BreadcrumbList` JSON-LD (3 separate `<script type="application/ld+json">` blocks, confirmed
live). The `/blog` listing page itself has **none** — no `Blog`, `CollectionPage`, or
`ItemList` schema at all.

**Why it matters.** A `CollectionPage`/`ItemList` on the listing page is a standard,
low-effort way to tell search engines and AI crawlers "this page is an index of these N
articles," reinforcing the site's blog as a coherent collection rather than a bag of
unconnected pages — same underlying theme as finding #3 (topical architecture).

**Recommended fix.** Add `Blog` or `CollectionPage` schema to `/blog`'s `<head>`, with an
`ItemList` of the posts currently displayed (title, url, image, datePublished per item —
data already available from the same Sanity query the page uses to render post cards).

## Note on FAQ schema (context, not an action item here)

Every post's `FAQPage` JSON-LD is correctly implemented and should stay — but as of **May 7,
2026** (verified via Tavily, not training-data guesswork), Google fully deprecated FAQ rich
results in Search for all sites, not just the August-2023 restriction to government/health
sites. The visual SERP dropdown this schema used to be able to earn no longer exists for
anyone. It's still worth keeping — FAQ markup continues to help AI answer engines
(ChatGPT/Perplexity/Google AI Overviews & AI Mode) extract and cite Q&A content, and helps
Google's own crawler parse the page — just don't expect a rich-snippet visual payoff from it
going forward. No frontend change needed; noted here so the mental model stays current.
