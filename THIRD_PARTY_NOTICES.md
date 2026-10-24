# Third-party notices

This plugin distills and adapts material from the following projects. Their
copyright notices are reproduced below as required by the Apache License 2.0.
A copy of the Apache License 2.0 is in `LICENSE`.

Every claim in this plugin's references was read at one of the snapshots named
below. The commit id is given so any statement can be checked against the exact
tree it came from.

## Google Agent Development Kit — Python (`google/adk-python`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-python

Snapshot: `12c7bed76` (2026-10-24) on `main`. The `v2.13.0` release tag is
`d68ca857` (2026-10-23), which sits on a release branch: it is the merge-base
`a221ccd8` plus twelve release-only commits (ten of them cherry-picks of later
`main` commits), and `main` carries 49 commits after that merge-base. Behaviour
was also measured against the installed `google-adk[eval]` 2.9.2, 2.12.0 and
2.13.0 wheels (and, at earlier snapshots, 2.10.0 and 2.11.0); where a change is
only on `main` and not in a release, the reference page says so. Previous
snapshot: `a6ed8590` (2026-10-17, after `v2.12.0`, tag `38381f65`).

Used as the authoritative source for the evaluation data model, criteria
catalog, CLI surface and agent-loading rules described in this plugin's
references. No source files are redistributed.

## Google ADK documentation (`google/adk-docs`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-docs

Snapshot: `cc53b6c14` (2026-10-23; previously `923938a4`). Changes since the
previous snapshot under `docs/evaluate/` are cosmetic; new since then are
`docs/agents/service-registration.md`, the Cloud Run sandbox flag in
`docs/deploy/cloud-run.md` and the Agent Lens link in `docs/integrations/weave.md`.

Used as a source for the condensed reference pages, including
`docs/context/caching.md` for the context-caching reference.

## Google ADK recipes (`google/adk-recipes`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-recipes

Snapshot: `a6d62a409` (2026-10-22; previously `05811bba`; the three commits
between them are README and documentation wording). The repository is
organised in three layers: reference patterns under `core/`, installable
plugin skills under `plugins/` (with an `EVAL.yaml` per skill), and deployable
services under `contrib/`. Its `AGENTS.md` still names `gemini-3.7-flash` as the
replacement model.

Used as a source for the model-deprecation policy this plugin applies, for the
`EVAL.yaml` shape `validate_eval_yaml.py` checks, and as background for the
project conventions its scaffolds follow. The scaffold
templates under `skills/agent-create/assets/templates/` are **original work**,
written for this plugin and informed by the recipes' layout — no recipe source
file is redistributed or derived from.

## Background reading

Fifteen Google-authored articles informed the context-caching, eval-engineering,
shadow-rollout, merge-gates, behavioural-tests, skills, context-engineering,
layouts, improvement-loop, user-simulation, case-taxonomy, state-and-memory,
workflows and CLI guidance. Nothing is reproduced from them: the references
paraphrase their ideas in this plugin's own words, cite them by title and URL in
their `Sources:` footers, and quote no more than a short phrase.

- Balaji Subramaniam, "How to slash token costs with context caching in agent
  harnesses", Google Cloud on Medium, 2026-09-01 —
  https://medium.com/google-cloud/how-to-slash-token-costs-with-context-caching-in-agent-harnesses-6431ba16d931
  (shared by Google Cloud Tech at
  https://x.com/GoogleCloudTech/status/2095201318348722507).
- Vlad Kolesnikov, "From 'vibe checks' to continuous evaluation: engineering
  reliable AI agents", Google Cloud blog, 2026-02-28 —
  https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents
  (shared by Google Cloud Tech as "6 ways to test AI agents with eval
  engineering", https://x.com/GoogleCloudTech/status/2095549949870289285).
- Charlotte Liang, "Eval-driven development: how we build better agent skills
  for Firebase", Firebase blog, 2026-08-11 —
  https://firebase.blog/posts/2026/08/eval-driven-development-agent-skills.
- Selbie, Zamora, Carah and DeMartino, "Evaluating AI agents: moving past the
  vibe check", Google Developers Blog, 2026-10-15 —
  https://developers.googleblog.com/evaluating-ai-agents-moving-past-the-vibe-check/
  (`merge-gates.md`, `maturity.md`, `tiered-pipeline.md`, `eval-engineering.md`,
  `batch-and-gating.md`).
- Ippolito and Saadati, "Three layers, one repo: a tour of google/adk-recipes",
  Google Developers Blog, 2026-10-15 —
  https://developers.googleblog.com/three-layers-one-repo-a-tour-of-googleadk-recipes/
  (`layouts.md`, `managing-evalsets.md`).
- Mullen and Gunderman, "The anatomy of harness engineering: how to evaluate,
  iterate, and guard AI coding agents", Google Developers Blog, 2026-09-09 —
  https://developers.googleblog.com/the-anatomy-of-harness-engineering-how-to-evaluate-iterate-and-guard-ai-coding-agents/
  (`behavioral-tests.md`, `case-taxonomy.md`).
- Nigam and Saboo, "Developer's guide to building ADK agents with skills",
  Google Developers Blog, 2026-04-01 —
  https://developers.googleblog.com/developers-guide-to-building-adk-agents-with-skills/
  (`skills-and-progressive-disclosure.md`).
- Hangfei Lin, "Architecting efficient context-aware multi-agent framework for
  production", Google Developers Blog, 2025-12-04 —
  https://developers.googleblog.com/architecting-efficient-context-aware-multi-agent-framework-for-production/
  (`context-engineering.md`, `state-and-memory.md`).
- Dima Melnyk and Jason Dai, "Driving the Agent Quality Flywheel from Your
  Coding Agent", Google Developers Blog, 2026-06-30 —
  https://developers.googleblog.com/driving-the-agent-quality-flywheel-from-your-coding-agent/
  (`improvement-loop.md`, `eval-engineering.md`, `cli-reference.md`,
  `merge-gates.md`).
- Stephen Allen, "How to Evaluate Live & Voice Agents in ADK", Google
  Developers Blog, 2026-08-24 —
  https://developers.googleblog.com/how-to-evaluate-live-voice-agents-in-adk/
  (`user-simulation.md`).
- Shubham Saboo and Eric Dong, "Build Long-running AI agents that pause,
  resume, and never lose context with ADK", Google Developers Blog,
  2026-05-12 —
  https://developers.googleblog.com/build-long-running-ai-agents-that-pause-resume-and-never-lose-context-with-adk/
  (`case-taxonomy.md`, `state-and-memory.md`).
- Alex Martin and Dima Melnyk, "Agent and Model Evaluations in Gemini
  Enterprise Agent Platform are now GA", Google Developers Blog, 2026-07-31 —
  https://developers.googleblog.com/agent-and-model-evaluations-in-gemini-enterprise-agent-platform-are-now-ga/
  (`improvement-loop.md`, `maturity.md`).
- Pier Paolo Ippolito and Elia Secchi, "Powering the Agent Development
  Lifecycle with any framework, from ADK to LangChain and beyond", Google
  Developers Blog, 2026-10-21 —
  https://developers.googleblog.com/powering-the-agent-development-lifecycle-with-any-framework-from-adk-to-langchain-and-beyond/
  (`cli-reference.md`, `workflows.md`, `merge-gates.md`, `improvement-loop.md`).
- Karl Weinmeister, "One Agent Development Kit, five languages", Google Cloud
  blog, 2026-10-13 —
  https://cloud.google.com/blog/topics/developers-practitioners/one-agent-development-kit-five-languages
  (`cli-reference.md`, `workflows.md`, `layouts.md`, `README.md`).
- Shubham Saboo and Eric Dong, "Build zero-trust AI agents with Google's Agent
  Development Kit", Google Developers Blog, 2026-08-17 —
  https://developers.googleblog.com/build-zero-trust-ai-agents-with-googles-agent-development-kit/
  (`behavioral-tests.md`, `app-and-plugins.md`).

## Not included

The methodology taught in the LinkedIn Learning course *Evaluating AI Agents
with Google ADK* (Jigyasa Grover) informed the evaluation practices described
here. That course's exercise files are licensed under the LinkedIn Learning
Exercise File License, which does not permit redistribution. No code, data,
evalset, checklist or prose from that repository is included in this plugin;
every reference page, template and fixture here is original work. The course is
credited as background reading only. (Snapshot consulted for the background
reading credit only: `17924c4`, unchanged at the 2026-10-17 and 2026-10-24
syncs.)
