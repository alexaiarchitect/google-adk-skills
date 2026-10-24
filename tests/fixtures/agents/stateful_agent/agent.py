"""Expense claim agent that keeps its place in session state (fixture: a
resumable step machine).

The claim's progress lives in state, not in the conversation: `claim_step`
moves new -> receipt_requested -> receipt_validated -> submitted, and each tool
is a checkpoint that writes the step it reached. A session that comes back days
later therefore resumes where it stopped, which is what the resume evalset
simulates by seeding `session_input.state`.
"""

import os
from typing import Any

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel, Field

FIRST_STEP = "new"


class ReceiptRequest(BaseModel):
    """The claim whose receipt is missing."""

    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$", description="Claim reference, CLM-000123.")


class SubmitRequest(BaseModel):
    """A claim to submit, with the claimant's payroll details."""

    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$")
    employee_id: str = Field(pattern=r"^E-[0-9]{5}$", description="Payroll id, E-12345.")
    cost_centre: str = Field(pattern=r"^CC-[0-9]{3}$", description="Cost centre, CC-123.")


class NotifyRequest(BaseModel):
    """Who to tell that a claim is waiting for approval."""

    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$")
    claimant_name: str = Field(min_length=2)


def start_claim(callback_context: CallbackContext) -> None:
    """Put a new session at the first step; a resumed one keeps its step."""
    if "claim_step" not in callback_context.state:
        callback_context.state["claim_step"] = FIRST_STEP
    return None


def request_receipt(req: ReceiptRequest, tool_context: ToolContext) -> dict[str, Any]:
    """Ask the claimant for the receipt of a claim and park the claim until it arrives.

    Call this when a claim has no receipt yet. Afterwards the claim waits at the
    receipt_requested step.

    Args:
        req: A ReceiptRequest naming the claim.

    Returns:
        A dict with `status` and the new `claim_step`; `status` "error" with the
        broken rule when the claim id is malformed.
    """
    try:
        req = ReceiptRequest.model_validate(req)
    except Exception as exc:  # pydantic.ValidationError
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    tool_context.state["claim_id"] = req.claim_id
    tool_context.state["claim_step"] = "receipt_requested"
    return {"status": "ok", "claim_step": "receipt_requested"}


def submit_claim(req: SubmitRequest, tool_context: ToolContext) -> dict[str, Any]:
    """Submit a claim whose receipt has been validated.

    Only call this at the receipt_validated step, after the claimant confirms.

    Args:
        req: A SubmitRequest with the claim id and the claimant's employee id and
            cost centre, taken from the claimant on file.

    Returns:
        A dict with `status` and the new `claim_step`; `status` "error" when the
        claim is not at receipt_validated or another claim is open.
    """
    try:
        req = SubmitRequest.model_validate(req)
    except Exception as exc:
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    if tool_context.state.get("claim_id") not in (None, req.claim_id):
        return {"status": "error", "error": "a different claim is open in this session"}
    if tool_context.state.get("claim_step") != "receipt_validated":
        return {"status": "error", "error": "the receipt has not been validated yet"}
    tool_context.state["claim_step"] = "submitted"
    return {"status": "ok", "claim_id": req.claim_id, "claim_step": "submitted"}


def notify_approver(req: NotifyRequest, tool_context: ToolContext) -> dict[str, Any]:
    """Tell the approver that a submitted claim is waiting for them.

    Call this right after `submit_claim` succeeds, never before.

    Args:
        req: A NotifyRequest with the claim id and the claimant's name.

    Returns:
        A dict with `status` and `notified` true; `status` "error" when the
        claim has not been submitted.
    """
    try:
        req = NotifyRequest.model_validate(req)
    except Exception as exc:
        return {"status": "error", "error": f"schema validation failed: {exc}"}
    if tool_context.state.get("claim_step") != "submitted":
        return {"status": "error", "error": "submit the claim before notifying the approver"}
    return {"status": "ok", "claim_id": req.claim_id, "notified": True}


INSTRUCTION = """You process one employee expense claim, step by step.

The claim is at step: {claim_step}
Claimant on file: {claimant?}

Follow these rules:
1. If the step is new and there is no receipt, call `request_receipt`.
2. If the step is receipt_requested, do NOT call any tool: tell the user you are
   still waiting for the receipt, whatever they ask.
3. If the step is receipt_validated and the user confirms, call `submit_claim`
   first and then `notify_approver`, using the claimant on file. Do not ask for
   details you already have.
Never approve or submit a claim without a receipt.
"""

root_agent = Agent(
    name="claims_agent",
    model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    description="Processes an expense claim through its steps, resuming where it stopped.",
    instruction=INSTRUCTION,
    tools=[request_receipt, submit_claim, notify_approver],
    before_agent_callback=start_claim,
)
