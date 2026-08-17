# SEO Blog Agent — Overview

**Rewritten 2026-08-17.** The previous version of this doc described an earlier design
("ContentSpark AI," Hugging Face Spaces + Cron-Job.org, a different worksheet schema, a
`get_brand_context_tool` that's since been left as dead code) that no longer matches what runs
today. For the full current architecture, stage-by-stage flow, and a ranked list of open gaps,
see **[`docs/pipeline_flow_and_state.md`](pipeline_flow_and_state.md)** — that's now the
authoritative flow doc. This page stays as the short entry point.

## What this is

An automated pipeline that researches a topic, drafts an SEO-optimized blog post in a defined
voice, verifies its own factual claims, and publishes it to Sanity CMS — with a human checkpoint
at the points that matter (new topic candidates, the final read before anything goes live) and
autonomous execution everywhere else. It publishes to
[owaisabdullah.dev](https://owaisabdullah.dev), a personal site covering AI, automation, web
development, and SaaS tooling topics.

## Why it exists

Written content that's actually worth publishing needs research, a consistent voice, and
fact-checking — work that used to mean either paying per-article or spending hours per post
doing it manually. The bet here is the same one every serious AI-content pipeline makes: a
model alone writes the average of what's already been said, which is why "generate and
publish" produces AI slop. The fix isn't a better prompt, it's a pipeline around the model —
real search-demand signal before writing, a fact-checked claims ledger built while drafting, a
defined voice instead of generic tone, and a human who actually reads the result before it goes
out under their name.

## The shape of it, briefly

- **Trigger & execution**: GitHub Actions runs each pipeline stage on a schedule (or on-demand),
  calling the agent code directly — no server, no HTTP hop.
- **Agents**: OpenAI Agents SDK, every model call retried across providers (Gemini →
  OpenRouter) on quota/transient failures.
- **State**: Google Sheets is the pipeline's database (topic queue, research, briefs, drafts,
  review feedback); Sanity CMS is the actual published destination.
- **Human interface**: a Discord bot — status queries, approve/reject reactions on drafts and
  topic candidates, on-demand stage triggers, targeted post edits.
- **Voice**: a static style guide (tone, banned words) plus a growing `brain/` folder of the
  owner's real first-hand stories, opinions, and numbers, pulled into drafts where relevant.

Full detail on every stage, what changed most recently, and what's still a real gap is in
[`docs/pipeline_flow_and_state.md`](pipeline_flow_and_state.md).

## Other docs in this folder

| Doc | What it's for |
|---|---|
| [`pipeline_flow_and_state.md`](pipeline_flow_and_state.md) | Current architecture, stage-by-stage flow, ranked gaps — start here for "how does this actually work" |
| [`service_setup.md`](service_setup.md) | Every credential/external connection needed, verified against the code |
| [`architecture_roadmap.md`](architecture_roadmap.md) | Third-party tools/services that could be added next (keyword-demand data, image-gen fallbacks) |
| [`incident_ledger.md`](incident_ledger.md) | Settled bugs and closed decisions, by subsystem — check before re-investigating something that "smells familiar" |
| [`seo_audit_internal_pipeline.md`](seo_audit_internal_pipeline.md) / [`seo_audit_external_frontend.md`](seo_audit_external_frontend.md) | Point-in-time SEO audit findings and fixes |
