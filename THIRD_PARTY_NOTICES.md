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

Snapshot: `175682204` (2026-11-05) on `main`, 31 commits after the previous
snapshot `70e1cbde7` (2026-11-04), none of them in a release (2.13.0, uploaded
2026-10-23, is still the latest on PyPI and `v2.13.0` the newest tag); before
that `70e1cbde7` was 32 commits after `ee8720162` (2026-11-02), none in a release
either; before that `ee8720162` was 63 commits after `dfd534a88` (2026-10-29; local checkout
`3b74a99bd`), none in a release either; before that `dfd534a88` was 87
commits after `12c7bed76` (2026-10-24), none in a release either; the reference
pages describe them under "Coming after 2.13.0 (unreleased main)" and keep
2.13.0 behaviour as the target. The `v2.13.0` release tag is
`d68ca857` (2026-10-23), which sits on a release branch: it is the merge-base
`a221ccd8` plus twelve release-only commits (ten of them cherry-picks of later
`main` commits), and `main` carries 49 commits after that merge-base. Behaviour
was also measured against the installed `google-adk[eval]` 2.9.2, 2.12.0 and
2.13.0 wheels (and, at earlier snapshots, 2.10.0 and 2.11.0); where a change is
only on `main` and not in a release, the reference page says so. Previous
snapshot before those: `a6ed8590` (2026-10-17, after `v2.12.0`, tag `38381f65`).

Used as the authoritative source for the evaluation data model, criteria
catalog, CLI surface and agent-loading rules described in this plugin's
references. No source files are redistributed.

## Google ADK documentation (`google/adk-docs`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-docs

Snapshot: `222adf7ee` (2026-11-05; previously `dd6f16c0f`, before that
`5f1b1b5b3`, before that `49c928f1e`, before that `cc53b6c14`, before that
`923938a4`). The two commits since `dd6f16c0f` add `docs/graphs/retry.md`
(retry and timeout defaults for graph nodes, covered in `workflow-graphs.md`) and
`docs/integrations/google-api-toolsets.md` (the Google Apps toolsets, covered in
`tool-crosscheck.md`). The four commits between `5f1b1b5b3` and `dd6f16c0f`: the Java 1.14.0 API reference and its Javadoc rendering,
review follow-ups to `docs/agents/models/google-gemini/deferred-schedule.md`
(the page dates from 2026-10-01; first covered here in `batch-and-gating.md`),
and the BigQuery agent analytics page (Go plugin, Python 2.13.0 updates
including the agent evaluation views, covered in `app-and-plugins.md`). The four
commits between `49c928f1e` and `5f1b1b5b3` touch
only the Java and TypeScript API references and Kotlin snippet CI, nothing for
Python. Nothing under `docs/evaluate/` changed in the latest twelve
commits; new are the Gemini 3.8 Live models and the voice-architectures
(speech-to-speech and cascade) pages under `docs/live/`, and the CLI reference
regenerated for 2.13.0 (version strings only). Earlier:
`docs/agents/service-registration.md`, the Cloud Run sandbox flag in
`docs/deploy/cloud-run.md` and the Agent Lens link in `docs/integrations/weave.md`.

Used as a source for the condensed reference pages, including
`docs/context/caching.md` for the context-caching reference.

## Google ADK recipes (`google/adk-recipes`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-recipes

Snapshot: `5eee5c911` (2026-11-05; previously `dc11493fa`, before that
`584a7e634`, before that `665f7b2a1`, before that `a6d62a409`, before that
`05811bba`). Since `dc11493fa`, three commits: genmedia-for-commerce (a newer
video model, variation rules) and a staged-ui-widgets deployable with a Windows
path test, nothing about ADK eval structure; unchanged but newly cited, the
`long-horizon-harness` journey evalset whose per-turn rubrics `EVS121` flags
(`rubric-authoring.md`). Between `584a7e634` and `dc11493fa`, six commits: genmedia-for-commerce fixes, CI action bumps, an image
link, and `core/python/ambient-quality-agent` v0.3.0, which raises its floor to
`google-adk>=2.13.0` and adds an OpenTelemetry plugin package — operational
changes, nothing about ADK eval structure. Between `665f7b2a1` and `584a7e634`: the new
`core/python/adk-voice-agent` (a Gemini Live voice agent with two `agents-cli`
eval suites; its eval layout is described in `layouts.md`, `live-agents.md` and
`user-simulation.md`, and none of its graders, prompts or datasets is copied) and
genmedia-for-commerce fixes. Since `a6d62a409`: `core/python/ambient-quality-agent` v0.2.0, and
`long-horizon-harness` moved 69 of its 78 eval cases to `agents-cli eval`
datasets (`af088392`), keeping two `adk eval` replay suites. The repository is
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

Thirty-one Google-authored articles (one co-written with CopilotKit) informed the context-caching,
eval-engineering, shadow-rollout, merge-gates, behavioural-tests, skills,
context-engineering, layouts, improvement-loop, user-simulation, case-taxonomy,
state-and-memory, workflows, maturity, rubric-authoring, live-agents,
workflow-graphs, app-and-plugins, model-policy, review-rule, calibration and CLI
guidance, plus the environment, reliability and tool-crosscheck notes. Nothing is reproduced from them: the references
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
- Pier Paolo Ippolito and Elia Secchi, "Hill-Climbing Agent Quality with Agents
  CLI Evaluation", Google Developers Blog, 2026-10-28 —
  https://developers.googleblog.com/hill-climbing-agent-quality-with-agents-cli-evaluation/
  (`improvement-loop.md`, `merge-gates.md`, `maturity.md`, `layouts.md`).
- Casey West, "Four evaluation engineering lessons learned from building agent
  plugins", Google Cloud Blog, 2026-10-17 —
  https://cloud.google.com/blog/topics/developers-practitioners/four-eval-engineering-lessons
  (`improvement-loop.md`, `rubric-authoring.md`).
- Remigiusz Samborski, "Behind the scenes, two months later: How we ship Google
  Agent Skills like products", Google Cloud Blog, 2026-10-29 —
  https://cloud.google.com/blog/topics/developers-practitioners/behind-the-scenes-two-months-later-how-we-ship-google-agent-skills-like-products
  (`merge-gates.md`, `maturity.md`, `rubric-authoring.md`, `case-taxonomy.md`).
- Stephen Allen and Kaz Sato, "Step by Step guide: Build a Voice Agent That
  Never Goes Silent using ADK", Google Developers Blog, 2026-10-19 —
  https://developers.googleblog.com/step-by-step-guide-build-a-voice-agent-that-never-goes-silent-using-adk/
  (`live-agents.md`, `user-simulation.md`).
- Shubham Saboo and Eric Dong, "Build Cross-Language Multi-Agent Team with
  Google's Agent Development Kit and A2A", Google Developers Blog, 2026-06-22 —
  https://developers.googleblog.com/en/build-cross-language-multi-agent-team-with-google-agent-development-kit-and-a2a/
  (`workflow-graphs.md`, `case-taxonomy.md`).
- Anant Nawalgaria, "Architecting Governed, Self-Evolving AI Agents on Google
  Cloud", Google Developers Blog, 2026-10-22 —
  https://developers.googleblog.com/architecting-governed-self-evolving-ai-agents-on-gemini-agent-platform/
  (`app-and-plugins.md`, one paragraph on governance gates).
- Héctor González Álvarez, "Who judges the judge? Three helpful ways to audit
  your LLM evaluators", Google Cloud Blog, 2026-10-20 —
  https://cloud.google.com/blog/topics/consulting/three-ways-to-audit-your-llm-evaluators
  (`calibration.md`, `rubric-authoring.md`, `metric-catalog.md`,
  `improvement-loop.md`; the five biases and three audit checks, paraphrased).
- Google A2UI team with Atai Barkai (CopilotKit), "Build Generative UI Apps with
  A2UI & CopilotKit Agents", Google Developers Blog, 2026-10-29 —
  https://developers.googleblog.com/build-generative-ui-apps-with-a2ui-copilotkit-agents/
  (`context-engineering.md`, one paragraph on a dedicated generation sub-agent).
- Siddharth Deekshit and Surekha Balla, "Scale agents securely with IAM Helper
  Agent Skills and MCP Tools", Google Cloud Blog, 2026-10-28 —
  https://cloud.google.com/blog/products/identity-security/scale-agents-securely-with-iam-helper-agent-skills-and-mcp-tools
  (`app-and-plugins.md`, one paragraph on tiered confirmation).
- Christina Lin, "The DevFest Community Workshop Experience: Building Real
  Agents Together", Google Cloud Blog, 2026-09-19 —
  https://cloud.google.com/blog/topics/developers-practitioners/the-devfest-community-workshop-experience-building-real-agents-together
  (`app-and-plugins.md`, `improvement-loop.md`; one sentence each).
- Karl Weinmeister and Alan Blount, "Five rules for evaluating agent skills
  before they bloat your context window", Google Cloud Blog, 2026-11-03 —
  https://cloud.google.com/blog/topics/developers-practitioners/evaluate-agent-skills-before-they-bloat-your-context-window
  (`skills-and-progressive-disclosure.md`, `case-taxonomy.md`, `merge-gates.md`,
  `maturity.md`, `review-checklist.md`, `validation-rules.md`; trigger
  collisions, paired with/without runs, separate verdicts, clean sandboxes,
  habits over documentation; the `AGT083` check; one phrase quoted).
- Sergio Villani, "Cost per task: Using cheap classifiers to route across
  Gemini models and thinking levels", Google Developers Blog, 2026-11-03 —
  https://developers.googleblog.com/cost-per-task-using-cheap-classifiers-to-route-across-gemini-models-and-thinking-levels/
  (`model-policy.md`, `workflow-graphs.md`, `improvement-loop.md`,
  `review-checklist.md`, `validation-rules.md`; the output cap as a ceiling and
  the `AGT084` check, classifier routing, cost per successful task; one phrase
  quoted, no figures reproduced).
- Burak Gokturk, Rujun Han and George Lee, "Introducing Physical-Engineering's
  Last Exam for agents", Google Cloud Blog, 2026-11-03 —
  https://cloud.google.com/blog/products/ai-machine-learning/introducing-physical-engineerings-last-exam-for-agents
  (`merge-gates.md`, `case-taxonomy.md`; gate-then-score, per-constraint versus
  per-deliverable rates, a held-out set, expert-validated long-horizon cases;
  one phrase quoted, no results reproduced).
- Remigiusz Samborski, "Google Cloud Developer plugin now available in
  Antigravity Marketplace", Google Cloud Blog, 2026-11-03 —
  https://cloud.google.com/blog/topics/developers-practitioners/google-cloud-developer-plugin-now-available-in-antigravity-marketplace
  (`skills-and-progressive-disclosure.md`, one sentence on discovery and
  guardrail skills).
- George Weale, Bo Yang and Andrew Biernat, "Optimizing a 200,000-line Python
  codebase overnight with Gemini 4 Argon", Google Developers Blog, 2026-10-13 —
  https://developers.googleblog.com/optimizing-a-200000-line-python-codebase-overnight-with-gemini-4-argon/
  (`environment.md`, `reliability.md`, `skills-and-progressive-disclosure.md`,
  `review-checklist.md`; the 2.12 startup and session-copy changes and the
  warm-up request, a hidden overhead benchmark with identical requests and
  medians, human review of agent-made changes, ADK's own `.agents/skills`
  layout; one phrase quoted, no figures reproduced).
- Ivan Nardini and Anant Nawalgaria, "Building an agent skill optimizer with
  AlphaEvolve, Harbor and Gemini 4 Argon", Google Cloud Blog, 2026-11-06 —
  https://cloud.google.com/blog/topics/developers-practitioners/how-to-build-an-agent-skill-optimizer-with-alphaevolve-and-harbor
  (`skills-and-progressive-disclosure.md`, `improvement-loop.md`,
  `merge-gates.md`; paired candidate and baseline runs, a holdout outside the
  search, the answer leak, search operations; one phrase quoted, no figures
  reproduced).

## Not included

The methodology taught in the LinkedIn Learning course *Evaluating AI Agents
with Google ADK* (Jigyasa Grover) informed the evaluation practices described
here. That course's exercise files are licensed under the LinkedIn Learning
Exercise File License, which does not permit redistribution. No code, data,
evalset, checklist or prose from that repository is included in this plugin;
every reference page, template and fixture here is original work. The course is
credited as background reading only. (Snapshot consulted for the background
reading credit only: `17924c4`, unchanged at the 2026-10-17, 2026-10-24,
2026-10-29, 2026-11-02, 2026-11-04 and 2026-11-05 syncs.)
