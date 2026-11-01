"""Expense claim batch reviewer (fixture: a throttle no released ADK accepts).

Each line of a submitted batch is reviewed by `review_claim`, an LlmAgent run as
a parallel worker. `max_parallel_workers=0` fails twice over (AGT085): ADK 2.13.0
and earlier have no such LlmAgent field (`Extra inputs are not permitted`), and
main after 2.13.0 (`5e4f0e18c`) requires at least 1. The function node
`normalize_line` caps its own workers, which `@node` has accepted since 2.4.0,
and must stay silent. Raises at import on every runner, by design.
"""

import os

from google.adk import Agent
from google.adk import Event
from google.adk import Workflow
from google.adk.apps import App
from google.adk.workflow import node
from pydantic import BaseModel


MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")


class ClaimReview(BaseModel):
    claim_line: str
    within_policy: bool
    reason: str


def split_batch(node_input: str):
    """Turns a pasted batch of claims into one line per claim."""
    return [line.strip() for line in node_input.splitlines() if line.strip()]


@node(parallel_worker=True, max_parallel_workers=4)
def normalize_line(node_input: str):
    yield " ".join(node_input.split())


review_claim = Agent(
    name="review_claim",
    model=MODEL,
    instruction=(
        "Review one expense claim line against policy: meals up to 60 dollars, "
        "equipment up to 400 dollars, travel up to 1500 dollars. Say whether it is "
        "within policy and why, in one sentence."
    ),
    parallel_worker=True,
    max_parallel_workers=0,
    output_schema=ClaimReview,
)


def summarize(node_input: list[ClaimReview]):
    return Event(
        message="\n".join(
            f"{review.claim_line}: {'ok' if review.within_policy else 'returned'} "
            f"({review.reason})"
            for review in node_input
        ),
    )


root_agent = Workflow(
    name="claim_batch_review",
    edges=[("START", split_batch, normalize_line, review_claim, summarize)],
)

app = App(name="parallel_workers_broken", root_agent=root_agent)
