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

Snapshot: `cbbdec3f` (2026-09-27), which is the `v2.10.0` release tag
(`53b3706e`) plus later commits on `main`. Behaviour was also measured against the
installed `google-adk[eval]` 2.9.2 and 2.10.0 wheels; where a change is only on
`main` and not in a release, the reference page says so.

Used as the authoritative source for the evaluation data model, criteria
catalog, CLI surface and agent-loading rules described in this plugin's
references. No source files are redistributed.

## Google ADK documentation (`google/adk-docs`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-docs

Snapshot: `b2bf03de` (2026-09-27).

Used as a source for the condensed reference pages.

## Google ADK recipes (`google/adk-recipes`)

    Copyright 2025-2026 Google LLC
    Licensed under the Apache License, Version 2.0
    https://github.com/google/adk-recipes

Snapshot: `ac86add4` (2026-09-27). In this snapshot the repository's shipped
vertical solutions moved from `skills/<vertical>/` to `plugins/<vertical>/`;
recipes remain under `core/` and `contrib/`.

Used as a source for the model-deprecation policy this plugin applies, and as
background for the project conventions its scaffolds follow. The scaffold
templates under `skills/agent-create/assets/templates/` are **original work**,
written for this plugin and informed by the recipes' layout — no recipe source
file is redistributed or derived from.

## Not included

The methodology taught in the LinkedIn Learning course *Evaluating AI Agents
with Google ADK* (Jigyasa Grover) informed the evaluation practices described
here. That course's exercise files are licensed under the LinkedIn Learning
Exercise File License, which does not permit redistribution. No code, data,
evalset, checklist or prose from that repository is included in this plugin;
every reference page, template and fixture here is original work. The course is
credited as background reading only. (Snapshot consulted for the background
reading credit only: `17924c4`.)
