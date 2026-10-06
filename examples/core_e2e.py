"""Deterministic OpenAI Agents SDK -> LoopGrid Core E2E.

Requires:
  pip install -e .
  LoopGrid Core at http://127.0.0.1:8000

No model API request is made: OpenAI Agents SDK ScriptedModel drives the real Runner/tool pipeline.
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
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def get_json(path: str):
    with urllib.request.urlopen(BASE_URL + path, timeout=10) as r:
        return json.load(r)


ready = get_json("/ready")
workspace = post_json("/api/v1/workspaces", {"name": f"openai-agents-e2e-{uuid.uuid4().hex[:8]}"})
workspace_id = workspace["workspace_id"]
client = LoopGrid(base_url=BASE_URL, workspace_id=workspace_id)
integration = LoopGridOpenAIAgents(client=client, workspace_id=workspace_id, agent_id="support-agent")
# E2E replaces the default OpenAI trace exporter so this deterministic test has no external trace network I/O.
set_trace_processors([integration.processor])

decision = integration.start_decision(
    decision_type="sandbox_refund",
    agent={"id": "support-agent", "version": "e2e"},
    authority={"acting_for": "LoopGrid E2E", "scope": ["refund:create"], "limit_usd": 100},
    model={"provider": "openai", "name": "scripted-model"},
    context={"prompt_version": "openai-agents-e2e-v1"},
    proposed_action={"tool": "sandbox_refund", "amount": 25, "currency": "USD"},
    metadata={"sandbox": True, "real_money_moved": False},
    policy={"policy_id": "e2e-policy", "version": "1", "decision": "auto_allowed"},
)
decision_id = decision["decision_id"]
executions = {"count": 0}


@tool
def sandbox_refund(amount: int) -> dict:
    """Execute a sandbox-only refund simulation."""
    executions["count"] += 1
    return {"status": "succeeded", "sandbox": True, "real_money_moved": False, "amount": amount}


model = ScriptedModel(
    [
        [function_call("sandbox_refund", {"amount": 25}, call_id="call_refund_1")],
        [assistant_message("Sandbox refund completed.")],
    ],
    emit_traces=True,
)
agent = Agent(name="Support agent", model=model, tools=[sandbox_refund])
run_config = RunConfig(
    trace_metadata=integration.trace_metadata(decision_id),
    trace_include_sensitive_data=False,
)
hooks = integration.run_hooks(decision_id)
result = Runner.run_sync(agent, "Handle the sandbox refund.", run_config=run_config, hooks=hooks)
model.assert_complete()
integration.flush()
integration.assert_healthy()
assert executions["count"] == 1

integration.record_outcome(
    decision_id,
    {"status": "succeeded", "sandbox": True, "real_money_moved": False, "external_reference": "sandbox-refund-1"},
)

detail = client.get_decision(decision_id)
summary = detail.get("summary", {})
coverage = detail.get("coverage", {})
verification = detail.get("verification", {})
state = summary.get("lifecycle", {}).get("state") if isinstance(summary.get("lifecycle"), dict) else summary.get("lifecycle")
score = coverage.get("score")
complete = coverage.get("complete")
valid = verification.get("valid")
failures = verification.get("failures", [])
assert state == "evidence_complete", detail
assert score == 100, detail
assert complete is True, detail
assert valid is True, detail
assert failures == [], detail

print(json.dumps({
    "coreVersion": ready.get("version"),
    "openaiAgentsVersionTarget": "0.23.1",
    "workspaceId": workspace_id,
    "decisionId": decision_id,
    "finalOutput": result.final_output,
    "toolExecutions": executions["count"],
    "lifecycle": state,
    "coverage": score,
    "coverageComplete": complete,
    "verifyValid": valid,
    "failures": failures,
    "eventTypes": [e.get("event_type") for e in detail.get("events", [])],
}, indent=2))
