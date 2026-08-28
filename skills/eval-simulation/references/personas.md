# Personas

## What a persona adds

A plan says what the user wants. A **persona** says how they behave while wanting
it: how much they volunteer, how much they push, how patient they are. The same
plan with two personas tests two different things.

## The pre-built three

| Persona | Behaves like |
|---|---|
| `EXPERT` | knows the domain, uses its vocabulary, supplies what is needed without prompting |
| `NOVICE` | vague, incomplete, needs guiding; the most productive default for finding missing-information failures |
| `EVALUATOR` | probes and tests, including edge cases and pushback |

Reference one by id in a scenario:

```json
"user_persona": "NOVICE"
```

Start with `NOVICE`. Most agents are built and demonstrated by someone who knows
exactly what to say, so the failures cluster where the user does not.

## A custom persona

A persona is a `UserPersona`: an `id`, a `description`, and a list of
`UserBehavior`s. **Every behaviour needs all four of its fields** — omitting
`violation_rubrics` is a validation error, not a default.

```json
{
  "id": "hurried_claimant",
  "description": "An employee filing expenses at the end of a long trip, who gives the minimum detail and expects the agent to keep up.",
  "behaviors": [
    {
      "name": "omits_detail",
      "description": "Leaves out values the agent needs until it asks for them.",
      "behavior_instructions": [
        "State the amount and the category when you make a request.",
        "Never volunteer the cost centre unless the agent asks for it directly.",
        "If the agent asks twice for the same value, say you already gave it and do not repeat it."
      ],
      "violation_rubrics": [
        "The generated user response supplies the cost centre before the agent asked for it.",
        "The generated user response invents a detail that is not in the conversation plan.",
        "The generated user response repeats a value it has already given the agent."
      ]
    }
  ]
}
```

Both list fields are **`list[str]`**. A single string in `behavior_instructions`
is rejected, and so is a `Rubric` object in `violation_rubrics` — these are plain
sentences, not the `{rubric_id, rubric_content}` shape used by eval criteria.

Three parts:

- **`description`** — who they are, in a sentence. Sets the voice.
- **`behavior_instructions`** — what they actually do, one instruction per entry.
  This is the operative part, and each line should be specific enough to be
  reproducible: "never volunteer the cost centre unless asked directly" produces
  consistent behaviour, "is impatient" does not.
- **`violation_rubrics`** — how to tell the simulator broke character. This is
  the part most often misread.

## Violation rubrics judge the *user*, not the agent

ADK is explicit: these rubrics "evaluate whether the user simulator presents the
behavior. If the user response presents any of these violations, the evaluator
will consider the user simulator response as invalid."

So a violation rubric is a sentence about the **generated user turn**, and the
metric that reads them is `per_turn_user_simulator_quality_v1`. ADK's own
built-in personas are written that way throughout — every rubric on `NOVICE`
starts "The Generated User Response ...":

```
The Generated User Response provides details for a high-level goal that was
already completed.
The Generated User Response invents details not provided in the Conversation
Plan or in the Agent's messages.
```

**An assertion about the agent does not belong here.** "The agent asks for a
value the user already supplied" is a claim about the agent, so it goes in a
criterion rubric on `rubric_based_final_response_quality_v1` or
`rubric_based_tool_use_quality_v1` via `/adk:eval-criteria`. Put it in
`violation_rubrics` and you will be told your *simulator* is broken every time
your *agent* misbehaves, which is the opposite of what you wanted.

The coupling that is real: a persona that withholds a value ships with the
rubrics that catch the simulator handing it over anyway, so the persona stays
honest across runs. The assertion about whether the agent coped is a separate,
deliberate piece of the eval config.

## Referencing a persona

Two forms, both on `conversation_scenario.user_persona`:

```json
"user_persona": "NOVICE"
```

resolves through the default registry, and

```json
"user_persona": {"id": "hurried_claimant", "description": "...", "behaviors": [...]}
```

inlines a custom one in the eval case itself. There is no separate persona file
that ADK loads by convention — a persona lives either in the registry or in the
case.

## Which personas to write

Pick them from the failures you actually fear:

| Fear | Persona |
|---|---|
| the agent guesses instead of asking | withholds a required value |
| the agent yields to pressure | claims urgency and prior approval |
| the agent loses context | states a constraint once, early |
| the agent cannot recover | changes their mind mid-conversation |
| the agent over-explains | wants one-line answers |

Two or three well-chosen personas beat a catalogue. Each one multiplies your
scenario count and your judge cost.

## Personas and cost

Every persona-scenario pair is a separate eval case, with its own conversation and
its own judge calls. Three personas across five scenarios is fifteen cases. Pair
personas with the scenarios they are actually informative for, rather than taking
the cross product.

---

Sources: `adk-python: evaluation/simulation/user_simulator_personas.py`
(`UserBehavior.violation_rubrics` docstring), `evaluation/simulation/pre_built_personas.py`
(the `NOVICE` rubrics quoted above), `evaluation/eval_case.py` (`ConversationScenario.user_persona`);
`adk-docs: docs/evaluate/user-sim.md` ("Custom personas").
