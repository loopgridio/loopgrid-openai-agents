"""Real OpenAI Agents SDK approval/pause/resume -> LoopGrid Core E2E.

Uses ScriptedModel so there is no model provider API call, while still exercising the real
OpenAI Agents SDK tool approval and RunState resume path.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

from agents import Agent, RunConfig, Runner, set_trace_processors
from agents.decorators import tool
from agents.testing import ScriptedModel, assistant_message, function_call
from loopgrid import LoopGrid
from loopgrid_openai_agents import LoopGridOpenAIAgents

BASE_URL = os.getenv("LOOPGRID_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def post_json(path: str, body: dict):
    req = urllib.request.Request(BASE_URL + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def get_json(path: str):
    with urllib.request.urlopen(BASE_URL + path, timeout=10) as r:
        return json.load(r)


ready = get_json("/ready")
workspace = post_json("/api/v1/workspaces", {"name": f"openai-agents-approval-{uuid.uuid4().hex[:8]}"})
workspace_id = workspace["workspace_id"]
client = LoopGrid(base_url=BASE_URL, workspace_id=workspace_id)
integration = LoopGridOpenAIAgents(client=client, workspace_id=workspace_id, agent_id="support-agent")
set_trace_processors([integration.processor])

decision = integration.start_decision(
    decision_type="sandbox_refund",
    agent={"id": "support-agent", "version": "approval-e2e"},
    authority={"acting_for": "LoopGrid E2E", "scope": ["refund:create"], "limit_usd": 100},
    model={"provider": "openai", "name": "scripted-model"},
    context={"prompt_version": "openai-agents-approval-v1"},
    proposed_action={"tool": "sandbox_refund", "amount": 25, "currency": "USD"},
    metadata={"sandbox": True, "real_money_moved": False},
    policy={"policy_id": "approval-policy", "version": "1", "decision": "human_approval_required"},
)
decision_id = decision["decision_id"]
executions = {"count": 0}


@tool(needs_approval=True)
def sandbox_refund(amount: int) -> dict:
    """Execute a sandbox-only refund simulation."""
    executions["count"] += 1
    return {"status": "succeeded", "sandbox": True, "real_money_moved": False, "amount": amount}


model = ScriptedModel(
    [
        [function_call("sandbox_refund", {"amount": 25}, call_id="call_refund_approval")],
        [assistant_message("Approved sandbox refund completed.")],
    ],
    emit_traces=True,
)
agent = Agent(name="Support agent", model=model, tools=[sandbox_refund])
run_config = RunConfig(trace_metadata=integration.trace_metadata(decision_id), trace_include_sensitive_data=False)
hooks = integration.run_hooks(decision_id)

first = Runner.run_sync(agent, "Handle the sandbox refund.", run_config=run_config, hooks=hooks)
assert first.interruptions, "expected OpenAI Agents SDK approval interruption"
assert executions["count"] == 0, "tool executed before approval"
integration.flush()

reviewer = "reviewer.loopgrid-e2e"
integration.record_human_review(decision_id, reviewer=reviewer, approved=True, reason="Approved sandbox action")
state = first.to_state()
for interruption in first.interruptions:
    state.approve(interruption)

resumed = Runner.run_sync(agent, state, run_config=run_config, hooks=hooks)
model.assert_complete()
integration.flush()
integration.assert_healthy()
assert executions["count"] == 1

integration.record_outcome(decision_id, {"status": "succeeded", "sandbox": True, "real_money_moved": False})
detail = client.get_decision(decision_id)
event_types = [e.get("event_type") for e in detail.get("events", [])]
assert event_types.index("human_approved") < event_types.index("tool_requested"), event_types
summary = detail.get("summary", {})
coverage = detail.get("coverage", {})
verification = detail.get("verification", {})
state_name = summary.get("lifecycle", {}).get("state") if isinstance(summary.get("lifecycle"), dict) else summary.get("lifecycle")
assert state_name == "evidence_complete", detail
assert coverage.get("score") == 100 and coverage.get("complete") is True, detail
assert verification.get("valid") is True and verification.get("failures", []) == [], detail

print(json.dumps({
    "coreVersion": ready.get("version"),
    "openaiAgentsVersionTarget": "0.23.1",
    "workspaceId": workspace_id,
    "decisionId": decision_id,
    "reviewer": reviewer,
    "toolExecutions": executions["count"],
    "finalOutput": resumed.final_output,
    "lifecycle": state_name,
    "coverage": coverage.get("score"),
    "coverageComplete": coverage.get("complete"),
    "verifyValid": verification.get("valid"),
    "failures": verification.get("failures", []),
    "eventTypes": event_types,
}, indent=2))
