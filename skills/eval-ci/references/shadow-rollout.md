# Shadow rollout: evaluate the revision before it takes traffic

The PR and nightly tiers gate **code**. A deployed agent can still regress
between "the code passed" and "users are talking to it": a different model id in
the service's environment, a missing secret, a tool backend the CI runner never
reached. The shadow pattern closes that gap by separating two steps people
usually run as one:

- **deploy** — the new revision exists and can be called;
- **release** — the new revision receives user traffic.

Between them, evaluate. Only a passing revision is released.

This plugin ships **no template** for it. Deploy targets, approval rules and
rollback policies differ too much between teams for one file to be right, and a
generated release pipeline that is subtly wrong is worse than none. What follows
is the pattern and a sketch to adapt; the gate in it is the same
`run_evals.py` the generated workflows already use.

## The Cloud Run mechanics

`adk deploy cloud_run` passes everything after `--` through to `gcloud run
deploy`, refusing only the flags ADK sets itself (`--project`, `--region`,
`--port`, `--source`, `--update-env-vars` and so on). Two flags make a shadow:

```bash
SHA=$(git rev-parse --short HEAD)
adk deploy cloud_run --project "$PROJECT" --region "$REGION" \
    --service_name claims-agent app \
    -- --no-traffic --tag "sha-$SHA" --no-allow-unauthenticated
```

- `--no-traffic` creates the revision with 0% of traffic. (It cannot be used on
  the very first deploy of a service; create the service once normally.)
- `--tag sha-<sha>` gives that revision its own URL,
  `https://sha-<sha>---<service>-<hash>.<region>.run.app`, and ties it to the
  commit. A tag must start with a letter, hence the prefix.

Promote by tag once the evaluation passes, and roll back the same way:

```bash
gcloud run services update-traffic claims-agent --region "$REGION" --to-tags "sha-$SHA=100"
gcloud run services update-traffic claims-agent --region "$REGION" --to-revisions "<previous-revision>=100"
```

A gradual release is the same command with a smaller number (`=10`), followed by
a second evaluation window.

## What to evaluate, and with what

Two layers, and it is worth being clear which one is the gate:

1. **The commit, with the revision's configuration — the gate.** `adk eval` runs
   the agent in-process; it cannot call a URL. So the gate is `run_evals.py` on
   the exact commit the revision was built from, with the **same environment**
   the service has: the same `MODEL_NAME`, the same backend variables, the same
   config file. Exit 0 promotes; 1 (a failed case, or under `--not-evaluated
   fail` a never-scored one) and 2 (could not run) stop the release. The SHA tag is what
   makes "the code that passed" and "the revision being promoted" provably the
   same thing.
2. **The deployed revision — a smoke check.** A handful of requests against the
   tagged URL, through ADK's API server routes, proves the revision starts,
   authenticates and reaches its tools:

   ```bash
   URL=$(gcloud run services describe claims-agent --region "$REGION" --format json \
         | python3 -c 'import json,sys; print([t["url"] for t in json.load(sys.stdin)["status"]["traffic"] if t.get("tag")==sys.argv[1]][0])' "sha-$SHA")
   TOKEN=$(gcloud auth print-identity-token)
   curl -sf -X POST "$URL/apps/app/users/ci/sessions/smoke-$SHA" -H "Authorization: Bearer $TOKEN"
   curl -sf -X POST "$URL/run" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"appName":"app","userId":"ci","sessionId":"smoke-'"$SHA"'",
          "newMessage":{"role":"user","parts":[{"text":"Submit claim EXP-1042 for 42.50 EUR, cost centre CC-310"}]}}' \
     | python3 -c 'import json,sys; ev=json.load(sys.stdin); names=[p["functionCall"]["name"] for e in ev for p in (e.get("content") or {}).get("parts",[]) if "functionCall" in p]; print(names); sys.exit(0 if "lookup_policy" in names else 1)'
   ```

   Treat it as a smoke test, not a suite: a few cases, deterministic checks on
   the returned events (which tools ran), no judge. Scoring a full suite against
   the URL means writing an inference client that replays each case's turns,
   collects the final response and the events, and scores them — the Google Cloud
   article does exactly that, with requests run in parallel under a semaphore.
   It is real work, outside what this plugin automates.

`gcloud run deploy` also prints the tagged URL; check the two agree the first
time you wire this up.

## A GitHub Actions sketch

Not a template — a shape. The `gate` job is the PR tier you already have, run
on the commit being released.

```yaml
name: release (shadow, evaluate, promote)
on: { push: { tags: ["v*"] }, workflow_dispatch: {} }
permissions: { contents: read, id-token: write }
env: { SERVICE: claims-agent, REGION: europe-west1 }

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install "google-adk[eval]==2.11.*"
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.WIF_PROVIDER }}
          service_account: ${{ vars.WIF_SERVICE_ACCOUNT }}
      - run: >
          python3 .github/adk-skills/skills/eval-run/scripts/run_evals.py app
          --config tests/eval/eval_config.json --not-evaluated fail
        env: { MODEL_NAME: "${{ vars.MODEL_NAME }}", GOOGLE_GENAI_USE_VERTEXAI: "TRUE",
               GOOGLE_CLOUD_PROJECT: "${{ vars.GOOGLE_CLOUD_PROJECT }}",
               GOOGLE_CLOUD_LOCATION: "${{ vars.GOOGLE_CLOUD_LOCATION }}" }

  shadow:
    needs: gate
    runs-on: ubuntu-latest
    steps:
      - # checkout, setup-python, pip install, auth as above, then:
        run: |
          adk deploy cloud_run --project "${{ vars.GOOGLE_CLOUD_PROJECT }}" --region "$REGION" \
            --service_name "$SERVICE" app -- --no-traffic --tag "sha-${GITHUB_SHA::7}"
      - # the smoke check from above, against the sha-<sha> URL
        run: ./ci/smoke.sh "sha-${GITHUB_SHA::7}"

  promote:
    needs: shadow
    environment: production        # optional manual approval
    runs-on: ubuntu-latest
    steps:
      - # auth as above, then:
        run: gcloud run services update-traffic "$SERVICE" --region "$REGION" --to-tags "sha-${GITHUB_SHA::7}=100"
```

What makes it a gate rather than a ritual: `promote` depends on `shadow`, which
depends on `gate`, and `gate` fails on `run_evals.py`'s exit code — never on
bare `adk eval`, which exits 0 whatever happened. The same rules as the other
tiers apply: pin the ADK version, upload `.adk/eval_history/` with
`if: always()`, fail on `NOT_EVALUATED`.

## When it is worth it

For an agent that already has a PR tier and a nightly tier, takes real traffic,
and is deployed more often than someone can watch each release. Before that, the
order is the usual one: cases (L1), then a gate on every change (L2). A shadow
rollout on top of a suite with no refusal case evaluates nothing that matters.

---

Sources: `adk-python: cli/cli_tools_click.py` (`adk deploy cloud_run`, the `--`
pass-through), `cli/deployers/_cloud_run_deployer.py` (the ADK-managed flags it
refuses), `cli/api_server.py` (`RunAgentRequest`, `/run`, the session route),
read on the installed 2.11.0 wheel; this plugin's `run_evals.py` (exit codes).
Google Cloud: `gcloud run deploy --no-traffic --tag` and `gcloud run services
update-traffic --to-tags` (Cloud Run docs, "Rollbacks, gradual rollouts, and
traffic migration"). The deploy-versus-release split, the SHA-tagged
`--no-traffic` revision, evaluating it before `update-traffic`, and the parallel
inference client are paraphrased from Vlad Kolesnikov, "From 'vibe checks' to
continuous evaluation: engineering reliable AI agents", Google Cloud blog,
2026-02-28,
https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents.
