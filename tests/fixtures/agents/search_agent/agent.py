"""Per-diem research agent (fixture: a built-in tool under the trajectory judge).

The agent answers per-diem questions for travel claims with Gemini's built-in
`google_search`, which reaches the request as a `types.Tool` with no function
declarations. Its eval config grades conversations with
`rubric_based_multi_turn_trajectory_quality_v1`, whose judge on ADK 2.13.0 and
earlier iterates those declarations and raises
`TypeError: 'NoneType' object is not iterable` (CFG076).
"""

import os

from google.adk.agents import Agent
from google.adk.tools import google_search

INSTRUCTION = """You help employees check travel per-diem rates before they file
an expense claim.

1. Always search for the current per-diem rate for the city the employee names.
2. Quote the rate and the source you found it on.

If the employee has not named a city, ask for it. Do NOT guess a rate you did
not find.
"""

root_agent = Agent(
    name="per_diem_agent",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Looks up travel per-diem rates for expense claims.",
    instruction=INSTRUCTION,
    tools=[google_search],
)
