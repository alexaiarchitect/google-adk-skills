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

Snapshot: `068610f2` (2026-10-12), which is the `v2.11.0` release tag
(`9f7965e7`, 2026-10-02) plus 205 later commits on `main`. Behaviour was also
measured against the installed `google-adk[eval]` 2.9.2 and 2.11.0 wheels (and,
at the previous snapshot `cbbdec3f`, 2.10.0); where a change is only on `main`
and not in a release, the reference page says so.

Used as the authoritative source for the evaluation data model, criteria
catalog, CLI surface and agent-loading rules described in this plugin's
references. No source files are redistributed.

## Google ADK documentation (`google/adk-docs`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-docs

Snapshot: `285f6412` (2026-10-12).

Used as a source for the condensed reference pages, including
`docs/context/caching.md` for the context-caching reference.

## Google ADK recipes (`google/adk-recipes`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-recipes

Snapshot: `d079ffce` (2026-10-09). The repository's shipped vertical solutions
live under `plugins/<vertical>/`; recipes remain under `core/` and `contrib/`.
Its `AGENTS.md` now names `gemini-3.7-flash` as the replacement model.

Used as a source for the model-deprecation policy this plugin applies, and as
background for the project conventions its scaffolds follow. The scaffold
templates under `skills/agent-create/assets/templates/` are **original work**,
written for this plugin and informed by the recipes' layout — no recipe source
file is redistributed or derived from.

## Background reading

Three Google-authored articles informed the context-caching, eval-engineering
and shadow-rollout guidance. Nothing is reproduced from them: the references
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

## Not included

The methodology taught in the LinkedIn Learning course *Evaluating AI Agents
with Google ADK* (Jigyasa Grover) informed the evaluation practices described
here. That course's exercise files are licensed under the LinkedIn Learning
Exercise File License, which does not permit redistribution. No code, data,
evalset, checklist or prose from that repository is included in this plugin;
every reference page, template and fixture here is original work. The course is
credited as background reading only. (Snapshot consulted for the background
reading credit only: `17924c4`, unchanged at the 2026-10-11 sync.)
