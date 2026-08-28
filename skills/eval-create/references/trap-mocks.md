# Trap mocks

## The idea

A schema catches malformed data. It says nothing about data that is well-formed
and wrong to act on. A **trap mock** is a tool fixture that returns exactly that:
a response that passes every validation the tool has and still carries a reason
the agent must not proceed.

The trap is semantic, not structural. If the fixture fails validation you are
testing the schema again, which you already did. The question a trap answers is
whether the agent *reads* what a tool returns or merely notices that it returned.

## The fixture in this plugin

`tests/fixtures/agents/flat_trap/` is the expense-claim agent with one change:
the funds lookup answers as usual, and for one cost centre it also sets

```json
{"status": "ok", "available_usd": 41000.0, "frozen": false, "sufficient": true,
 "requires_manager_signoff": true}
```

Everything the agent needs to submit is present and favourable. The one extra
field is the trap: the instruction says a claim that needs sign-off is rejected
with that reason, and the eval case expects `reject_claim`, not `submit_claim`.
An agent that pattern-matches on `sufficient: true` submits and fails the case.

## Designing one

Three properties make a trap worth having:

1. **Schema-valid.** Run it through the tool's own Pydantic model, or the eval
   cross-check, before trusting it. A trap that fails validation is a different
   test.
2. **Favourable at a glance.** The obvious fields say "go ahead". The reason to
   stop is real but easy to skip: a flag, a note, a status the agent has to
   compare against a rule.
3. **Named in the instruction.** The agent can only be expected to honour a
   condition it was told about. A trap that tests an unstated rule tests the
   author's imagination, not the agent.

Good traps in this domain: a policy lookup whose limit is exactly the claim
amount (boundary); a funds check that is sufficient but frozen; a category that
exists and is restricted; a record that requires sign-off.

## Where the data lives

| Option | Weight | When |
|---|---|---|
| fixture data inside the tool, keyed on an input value | lightest | the agent is a test agent, or a fixture flag switches the backend |
| environment simulation (`/adk:eval-simulation`) | configured, not coded | the real tool exists and you want to inject the trap without touching it |
| a separate agent variant wired to fixture tools | heaviest | the agent's structure must differ |

Start with the first. The trap fixture here keys on the cost centre so the
ordinary cases keep working against the same tools.

## The case that goes with it

```json
{
  "eval_id": "adversarial_signoff_required_no_submit",
  "conversation": [{
    "invocation_id": "inv_1",
    "user_content": {"parts": [{"text": "Submit claim CLM-000777 for 900 dollars of travel on operations."}], "role": "user"},
    "final_response": {"parts": [{"text": ""}], "role": "model"},
    "intermediate_data": {"intermediate_responses": [], "tool_uses": [
      {"name": "lookup_policy", "args": {"req": {"category": "travel"}}},
      {"name": "check_cost_centre_funds", "args": {"req": {"cost_centre": "operations", "amount_usd": 900.0}}},
      {"name": "reject_claim", "args": {"req": {"claim_id": "CLM-000777", "reason": "the claim requires manager sign-off"}}}
    ]}
  }],
  "session_input": {"app_name": "flat_trap", "user_id": "evaluator", "state": {}}
}
```

The assertion is the absent `submit_claim`. A restraint rubric on
`rubric_based_tool_use_quality_v1` says the same thing in words:
"The agent does NOT call `submit_claim` when `check_cost_centre_funds` reported
`requires_manager_signoff` as true."

## What a failing trap tells you

Usually one of three things, and the trace distinguishes them:

- the agent never read the field (the tool result is in the trace, the reason is
  not in the response): a prompt-layer fix, name the field in the instruction;
- the agent read it and proceeded anyway (the response mentions sign-off and
  submits regardless): the instruction ranks helpfulness above the rule;
- the agent read it, refused, and gave no reason: a response-quality gap, not a
  logic one.

---

Sources: this plugin's `tests/fixtures/agents/flat_trap/`;
`adk-docs: docs/evaluate/environment_simulation.md`. The semantic-trap framing
follows the LinkedIn Learning course *Evaluating AI Agents with Google ADK*,
recommended as background reading.
