# Context caching

An agent that carries a long procedure, a policy manual or a large tool list
sends the same tokens on every model call. Context caching stores that prefix
once and lets later calls read it at a discount. It pays off in exactly one shape
of agent — a long, unchanging prefix reused over several turns — and costs a
little in every other shape. This page is about telling the two apart, and about
proving the gain with the eval suite rather than assuming it. Caching makes a
prefix cheaper; shrinking what is in the request at all (skills loaded on
demand, compaction, artifacts) is the other half:
[context-engineering.md](context-engineering.md).

## What ADK gives you

Caching is switched on for a whole app, on the `App`:

```python
from google.adk.agents import Agent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App

root_agent = Agent(
    name="claims_agent",
    model=MODEL,
    static_instruction=EXPENSE_POLICY_SOP,   # long, literal, never changes
    instruction="The claimant's cost centre is {cost_centre?}.",  # per-turn part
    tools=[lookup_policy, check_cost_centre_funds, submit_claim, reject_claim],
)
app = App(
    name="claims_agent",
    root_agent=root_agent,
    context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=600, cache_intervals=5),
)
```

| `ContextCacheConfig` field | Default | Bounds | Meaning |
|---|---|---|---|
| `cache_intervals` | 10 | 1–100 | invocations that may reuse one cache before it is refreshed |
| `ttl_seconds` | 1800 | > 0 | lifetime of the cache |
| `min_tokens` | 0 | ≥ 0 | skip caching unless the **previous** request's prompt had at least this many tokens |
| `create_http_options` | none | — | `HttpOptions` for the cache-create call, e.g. a timeout; on timeout the request proceeds uncached |

The model is `extra="forbid"`: a misspelt field raises at construction. The
class is identical on 2.9.2, 2.11.0 and 2.12.0.

**`static_instruction` and `instruction` divide the prompt.** When
`static_instruction` is set, ADK sends it **verbatim** as the system instruction
— no `{var}` substitution, no provider call — and moves `instruction` (where
placeholders and an `InstructionProvider` still work) into the user content
after it. When `static_instruction` is unset, `instruction` *is* the system
instruction, so every placeholder value is part of the cached prefix.
**Setting `static_instruction` alone enables nothing**: without
`context_cache_config` no explicit cache is created. The Live API has its own
caching and ignores `static_instruction`.

## The second-turn rule and the size floor

ADK never creates a cache on the first model request of a session. It first
records a fingerprint, then creates the cache on a later request whose prefix
fingerprint matches; ADK's own docstring puts it as "caching begins on the second
turn of a session at the earliest". On top of `min_tokens`, ADK applies the
model's floor: **2,048 tokens for `gemini-2.5-*`, 4,096 for `gemini-3*`**. An
alias such as `gemini-flash-latest` gets no client-side floor and the service
decides.

Consequence for evaluation: **a single-turn eval case can never show a cache
hit.** If the suite has no multi-turn case, it cannot tell you whether caching
works.

## What breaks the prefix

ADK fingerprints the system instruction, the tool declarations (sorted, so
reordering them is harmless), the `tool_config`, and every content before the
last run of user messages. Any byte that changes there makes a new cache:

- **Dynamic text in `static_instruction`** — an f-string, `.format()`, a
  timestamp, a request id, `datetime.now()`. It is also sent unprocessed, so a
  `{var}` there reaches the model as literal braces.
- **Dynamic text in `instruction` when there is no `static_instruction`** —
  today's date, the user's name, the session's state.
- **A tool set that varies per request** — tools added conditionally, a toolset
  whose declarations depend on the caller, a docstring rendered with a date.
- **History rewritten mid-session** — anything that edits earlier contents
  changes the cached part.

The rule the harness article puts in one line — dynamic variables belong at the
end of the prompt, "never at the beginning" — is what `static_instruction` /
`instruction` encodes. Upstream's own `cache_analysis` experiment runner does the
opposite on purpose: it prepends a timestamp to the instruction so that runs do
not reuse each other's cache. Read it as the failure mode, demonstrated.

## When to turn it on

A decision table, paraphrasing the harness article's framework:

| Situation | Verdict |
|---|---|
| Agent's static context is below ~32k tokens | usually not worth it; implicit caching by the provider may already help |
| One- or two-turn flows | no — the first request only fingerprints, and one reuse rarely repays the write |
| The header changes every cycle (live data, timestamps up front) | no — fix the prompt order first, then reconsider |
| Iterative loops over a large static context: test-and-repair over a repository, a long SOP over many turns | yes |
| An eval suite that replays long multi-turn cases nightly | yes, if the agent itself qualifies; the judge calls are not cached |

The 32k figure is the article's rule of thumb for when the saving is worth the
machinery; ADK's own floors (2k / 4k) are only when caching is *possible*.

## The billing shape

Per the harness article: the first request that writes the cache bills its
prefix at full rate (1.0x), and each later read of it at a quarter (0.25x).
For a prefix of *P* tokens over *n* requests:

```
uncached:  n × P
cached:    P + (n − 1) × 0.25 × P        (plus storage for the TTL)
n = 5:     5P  →  2P    a 60% cut on the prefix's input cost
```

The article's own measurements, on a ~38k-token static prefix:

| Scenario | Turns | Input tokens transmitted, uncached → cached | Reduction |
|---|---|---|---|
| batch code modernization | 5 | 190,139 → 39,503 | 79.2% |
| red-team / blue-team security debate | 4 | 154,008 → 38,907 | 74.7% |
| dependency-graph refactor | 4 | 154,479 → 39,156 | 74.7% |

Token reduction is not cost reduction: output tokens, the cache write and
storage still bill. The article's summary for a 4–5 turn flow is roughly half
the cost. Check current Gemini pricing before quoting a number to anyone.

## Measuring it

**In eval results (2.10+).** `token_usage_v1` reports per invocation and per
case a `token_usage_details` block whose `cached_tokens` sits inside
`prompt_tokens`, inside `input_tokens`. `null` means no call reported it, not
zero. `/adk:eval-analyze`'s `summarize_results.py` adds a `cached` column and a
per-run cache-hit ratio (`cached_tokens / prompt_tokens`) when the counts are
there, plus a run-level "Context cache served X%" line; on 2.9 there are no
efficiency metrics and the figures read `n/a`. `discover.py`, `lint_agent.py`
and `load_agent.py` report `static_instruction_chars`,
`static_instruction_dynamic` and `context_cache_config` for each agent.

**In traces.** Each model response event carries `usage_metadata`, whose
`cached_content_token_count` is the number of prompt tokens served from cache
(`usageMetadata.cachedContentTokenCount` in `adk run --jsonl` output). The event
also carries `cache_metadata`: `cache_name`, `expire_time` and
`invocations_used` set means an active cache; all three null means
fingerprint-only, that is, no cache yet. On the `generate_content` span the
same count is `gen_ai.usage.cache_read.input_tokens`; on 2.11.0 the
`adk.experimental.context_cache.*` attributes (hit, fingerprint,
invocations used) appear only with `ADK_EXPERIMENTAL_TELEMETRY_FEATURES=context_cache`
or `ADK_EXPERIMENTAL_TELEMETRY=true`.

**For a session.** `google.adk.utils.cache_performance_analyzer.CachePerformanceAnalyzer`
summarizes one session's hit ratio, refresh count and cached tokens; the
upstream `cache_analysis` sample drives it.

## Proving the gain, the eval-driven way

1. Make sure the suite has a **multi-turn** case long enough to reuse the
   prefix (`AGT063` says when it has none).
2. Run the suite and keep the result: the baseline.
3. Split the instruction and add `context_cache_config` (or scaffold that way).
4. Run the same suite again.
5. Compare: `/adk:eval-analyze`'s `compare_runs.py <baseline> <candidate>
   [--latest]` reports pass rate, verdict flips, per-invocation prompt / cached /
   uncached-prompt / output tokens, the cache-hit ratio, seconds and call
   counts with deltas, ends with `REGRESSION`, `IMPROVED` or `NO REGRESSION`,
   and exits 1 if the candidate's pass rate dropped or a case flipped to failed.

A caching change that moves the pass rate is not a caching change: it changed
the prompt. Moving `instruction` into user content does alter what the model
sees, so the comparison is the check, not a formality.

## What this plugin does about it

**Scaffold.** `scaffold_agent.py --with-cache` writes the SOP as a literal
`static_instruction`, a short per-turn `instruction`, and
`ContextCacheConfig(min_tokens=2048, ttl_seconds=600, cache_intervals=5)` on the
`App` (the flat layout gains an `App` for it), plus a two-turn starter case. The
SOP becomes a literal `STATIC_INSTRUCTION`; the short `INSTRUCTION` carries
`{turn_context?}`. Combined with `--workflow` it is refused (exit 2).

**Review.** `lint_agent.py` reports:

| Id | Severity | Trigger |
|---|---|---|
| `AGT060` | warn | `static_instruction` is not byte-identical between requests: an f-string, `.format()`, `%` formatting, a `datetime` / `date` / `time` / `uuid` / `random` / `secrets` call, a `{var}` placeholder (never filled there), or an `InstructionProvider` callable or lambda. The text is followed through `types.Content` / `Part` / `Part.from_text`, lists, `+`, `dedent` and a name bound to a module constant |
| `AGT061` | info | an instruction of 8,000+ characters (about 2,000 tokens, the Gemini 2.5 floor) with no `static_instruction` and no `context_cache_config` on the App: nothing caches it |
| `AGT062` | **error** | a `ContextCacheConfig` literal out of bounds or with an unknown field (`cache_intervals` outside 1–100, `ttl_seconds` not > 0, `min_tokens` < 0), so building the App raises `ValidationError` |
| `AGT063` | info | caching is on, but every eval case is a single turn with no expected tool call, so each session makes one model request and the suite cannot show a hit. A case counts if it has two or more invocations, a `conversation_scenario`, or an expected tool call (the tool round-trip is a second request) |
| `AGT065` | warn | a plugin imported from a private module `google.adk.plugins._…`; import it from `google.adk.plugins` (`ToolCallIntegrityPlugin` / `Error` need 2.11+) |
| `AGT066` | **error** | (discovery) `ToolCallIntegrityPlugin` is used and the resolved runner is older than 2.11, so the import fails before the agent loads |
| `AGT067` | info | caching is configured but the estimated static prefix (`cache_prefix_tokens_estimate`: instruction chars / 4 plus ~60 tokens per tool) is below `max(min_tokens, 2048)`, so the cache rarely engages: grow the procedure, lower `min_tokens` only above the model floor, or drop the config |

There is no `AGT064`: `global_instruction` is already `AGT002`. With only
`static_instruction` set, `AGT020` (no instruction) no longer fires, and the
rule extractor and coverage matrix read `static_instruction` followed by
`instruction`.

## Upstream worked examples

- `contributing/samples/context_management/cache_analysis` — an 11,800-character
  literal `instruction`, seven tools, and `ContextCacheConfig(min_tokens=4096,
  ttl_seconds=600, cache_intervals=3)` on the App; `run_cache_experiments.py`
  runs it with and without caching and compares. The long text sits in
  `instruction`, not `static_instruction` — fine while it has no placeholders.
- `contributing/samples/context_management/static_instruction` — a literal
  `static_instruction` wrapped in `types.Content` plus an `InstructionProvider`
  for the mood that changes per turn: the split done right. It sets no
  `context_cache_config`, so it caches nothing explicitly; it shows the prompt
  order, not the saving.

---

Sources: `adk-python: agents/context_cache_config.py` (fields, bounds, the
second-turn docstring), `agents/llm_agent.py` (`static_instruction`, its effect on
`instruction`), `apps/app.py` (`context_cache_config`),
`models/gemini_context_cache_manager.py` (the fingerprint, the 2,048 / 4,096
floors), `models/cache_metadata.py`, `flows/llm_flows/context/_cache.py` (the
previous-request token count), `telemetry/_token_usage.py` and
`telemetry/tracing.py` (span attributes), `telemetry/context.py` (the
experimental-telemetry switches), `utils/cache_performance_analyzer.py`,
`evaluation/eval_metrics.py` (`TokenUsageDetails`),
`contributing/samples/context_management/cache_analysis` and `static_instruction`;
read on installed 2.9.2 and 2.11.0 wheels. `adk-docs: docs/context/caching.md`.
The decision framework, the prefix-ordering rule, the 0.25x / 1.0x billing shape
and the three measured scenarios are from Balaji Subramaniam, "How to slash token
costs with context caching in agent harnesses", Google Cloud on Medium,
2026-09-01, https://medium.com/google-cloud/how-to-slash-token-costs-with-context-caching-in-agent-harnesses-6431ba16d931,
paraphrased. The article quotes a 74.6–79.5% range; the reductions in the table
are recomputed here from its token counts.
