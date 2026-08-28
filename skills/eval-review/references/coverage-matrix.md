# Reading the coverage matrix

## What it is

A table of the agent's instruction, one row per rule, and the eval cases that
appear to exercise each one:

| # | kind | rule | covered by |
|---|---|---|---|
| 2 | mandatory | POLICY: Always call `lookup_policy` first... | `happy_travel_within_policy`, `edge_frozen_cost_centre_no_submit` |
| 6 | prohibition | Do NOT call `check_cost_centre_funds` if the category is restricted. | `edge_restricted_category_no_funds_check` |
| 9 | conditional | When you refuse, name the constraint you hit... | **nothing** |

## How rules are found

The instruction is split into sentences and bullets (wrapped lines are rejoined
first), then each is classified:

- **mandatory** — "always", "must", "first", "then", "before", "after"
- **prohibition** — an explicit negative: "never", "do not", "must not",
  "under no circumstances". Note that "reject" and "refuse" as ordinary verbs
  ("call `reject_claim`") are *not* prohibitions.
- **conditional** — "if", "when", "unless", "otherwise"
- **informational** — everything else, including the role statement

**Branches** are the prohibitions plus the conditionals: the points where the
agent's path forks.

## How cases are matched

A case covers a rule when it calls a tool the rule names, or when its text shares
at least two distinctive words with the rule. That is a heuristic. It is good at
finding rules with **no** plausible case, which is what you want it for, and it
will sometimes credit a case that only looks related. Read the matrix as
evidence, and never report an uncovered rule as a failure — report it as a
question.

## The branch rule

An instruction with *N* branches needs at least *N + 1* cases: one per branch,
plus the unobstructed happy path. It is a floor, not a target. Two branches that
interact (a restricted category *and* a frozen cost centre) may deserve a case
each plus one for the combination.

## Suite shape

Roughly:

| Layer | Share | What it is |
|---|---|---|
| narrow | ~70% | single-turn, single-concern, deterministic. Fast, cheap, and they localize a failure |
| multi-turn | ~20% | context carried across turns; where memory failures live |
| adversarial | ~10% | designed to make the agent fail |

Adjust for risk: an agent that spends money or writes to a system of record earns
a heavier adversarial layer than one that answers questions. The shape check
flags the two absences that matter most — **no adversarial case at all** (nothing
in the suite tries to break the agent) and **no multi-turn case** (context
retention is untested).

## Tool coverage

Three lists:

- **exercised** — tools some case expects to be called.
- **never exercised** — tools no case ever expects. Either the behaviour is
  untested, or the tool is dead weight in the agent's tool list, where it still
  costs prompt space and adds a way to go wrong.
- **unknown in cases** — names the cases use that the agent does not define.
  Always a bug, in one file or the other.

## What to do with the output

Work in this order:

1. Unknown tools — a real error in either the case or the agent.
2. Uncovered prohibitions — untested refusals are how agents do too much.
3. Uncovered conditionals — untested branches.
4. Shape gaps — add the missing adversarial and multi-turn layers.
5. Never-exercised tools — test them, or remove them.

---

Sources: this plugin's `lib/adk_skills/coverage.py`. The suite-shape and
branch-coverage practices follow the LinkedIn Learning course *Evaluating AI
Agents with Google ADK*, recommended as background reading.
