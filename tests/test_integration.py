from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from loopgrid_openai_agents import (
    LOOPGRID_OPENAI_AGENTS_VERSION,
    OPENAI_AGENTS_TARGET_VERSION,
    LoopGridOpenAIAgents,
)


class FakeClient:
    def __init__(self):
        self.events = []
        self.decisions = []
        self.raise_events = False
        self.lock = threading.Lock()

    def record_decision(self, **kwargs):
        self.decisions.append(kwargs)
        return {"decision_id": f"dec_{len(self.decisions)}"}

    def add_event(self, decision_id, event_type, payload, actor_type="system", actor_id="loopgrid", **kwargs):
        if self.raise_events:
            raise RuntimeError("transport unavailable: super secret details")
        with self.lock:
            self.events.append({"decision_id": decision_id, "event_type": event_type, "payload": payload, "actor_type": actor_type, "actor_id": actor_id, **kwargs})
        return self.events[-1]

    def policy_evaluated(self, decision_id, payload, actor_id="policy", **kwargs):
        return self.add_event(decision_id, "policy_evaluated", payload, "policy", actor_id, **kwargs)

    def human_approved(self, decision_id, reviewer, reason="", **kwargs):
        return self.add_event(decision_id, "human_approved", {"approved": True, "reviewer": reviewer, "reason": reason}, "human", reviewer, **kwargs)

    def human_rejected(self, decision_id, reviewer, reason="", **kwargs):
        return self.add_event(decision_id, "human_rejected", {"approved": False, "reviewer": reviewer, "reason": reason}, "human", reviewer, **kwargs)

    def outcome_observed(self, decision_id, payload, actor_id="outcome-observer", **kwargs):
        return self.add_event(decision_id, "outcome_observed", payload, "system", actor_id, **kwargs)


def integration(**kwargs):
    client = FakeClient()
    return LoopGridOpenAIAgents(client=client, agent_id="agent-1", **kwargs), client


def trace(trace_id="trace_1", decision_id="dec_1"):
    return SimpleNamespace(trace_id=trace_id, metadata={"loopgrid_decision_id": decision_id})


def span(kind, *, trace_id="trace_1", span_id="span_1", name="tool", input=None, output=None, model=None, usage=None, error=None):
    attrs = {"type": kind, "name": name, "input": input, "output": output, "model": model, "usage": usage, "model_config": {"temperature": 0}}
    data = SimpleNamespace(**attrs)
    return SimpleNamespace(trace_id=trace_id, span_id=span_id, parent_id="parent", span_data=data, error=error)


def start_kwargs():
    return dict(
        decision_type="refund",
        agent={"id": "agent-1"},
        authority={"acting_for": "merchant", "scope": ["refund:create"]},
        model={"provider": "openai", "name": "gpt-test"},
        context={"prompt_version": "v1"},
        proposed_action={"tool": "refund.create", "amount": 10},
    )


def test_versions():
    assert LOOPGRID_OPENAI_AGENTS_VERSION == "0.1.0"
    assert OPENAI_AGENTS_TARGET_VERSION == "0.23.1"


def test_requires_agent_id():
    x, _ = integration()
    kw = start_kwargs(); kw["agent"] = {}
    with pytest.raises(ValueError, match="agent.id"):
        x.start_decision(**kw)


def test_requires_authority():
    x, _ = integration()
    kw = start_kwargs(); kw["authority"] = {}
    with pytest.raises(ValueError, match="authority"):
        x.start_decision(**kw)


def test_requires_model_and_context():
    x, _ = integration()
    kw = start_kwargs(); kw["model"] = {}
    with pytest.raises(ValueError, match="model.name"):
        x.start_decision(**kw)
    kw = start_kwargs(); kw["context"] = {}
    with pytest.raises(ValueError, match="context"):
        x.start_decision(**kw)


def test_framework_metadata_and_no_invented_policy():
    x, c = integration()
    result = x.start_decision(**start_kwargs())
    assert result["decision_id"] == "dec_1"
    assert c.decisions[0]["metadata"]["framework"] == "openai-agents"
    assert not c.events


def test_explicit_policy_appended_after_decision():
    x, c = integration()
    kw = start_kwargs(); kw["policy"] = {"policy_id": "p", "version": "1", "decision": "auto_allowed"}
    x.start_decision(**kw)
    assert c.events[0]["event_type"] == "policy_evaluated"


def test_policy_validation():
    x, _ = integration()
    with pytest.raises(ValueError, match="provenance"):
        x.record_policy("dec", {"decision": "auto_allowed"})
    with pytest.raises(ValueError, match="policy.decision"):
        x.record_policy("dec", {"policy_id": "p", "decision": "maybe"})


def test_trace_metadata_is_decision_scoped_and_reserved():
    x, _ = integration()
    md = x.trace_metadata("dec_a", {"request_id": "r1"})
    assert md["loopgrid_decision_id"] == "dec_a"
    assert md["request_id"] == "r1"
    with pytest.raises(ValueError, match="reserved"):
        x.trace_metadata("dec_a", {"loopgrid_decision_id": "other"})


def test_generation_end_records_commitment_not_raw_content():
    x, c = integration()
    x.processor.on_trace_start(trace())
    x.processor.on_span_end(span("generation", output=[{"secret": "do-not-store"}], model="gpt-test", usage={"input_tokens": 1}))
    x.flush()
    e = c.events[-1]
    assert e["event_type"] == "model_completed"
    assert "output_commitment" in e["payload"]
    assert "output" not in e["payload"]
    assert "do-not-store" not in str(e)


def test_capture_content_true_records_model_output():
    x, c = integration(capture_content=True)
    x.processor.on_trace_start(trace())
    x.processor.on_span_end(span("generation", output=[{"text": "visible"}], model="gpt-test"))
    x.flush()
    assert c.events[-1]["payload"]["output"] == [{"text": "visible"}]


def test_function_spans_do_not_create_tool_evidence():
    x, c = integration()
    x.processor.on_trace_start(trace())
    x.processor.on_span_start(span("function", input='{"amount":10}', name="refund"))
    x.processor.on_span_end(span("function", output={"status": "ok"}, name="refund"))
    x.flush()
    assert c.events == []


def test_run_hooks_record_actual_tool_boundaries_without_outcome():
    import asyncio

    x, c = integration()
    hooks = x.run_hooks("dec_1")
    context = SimpleNamespace(
        tool_name="refund",
        tool_call_id="call_123",
        tool_arguments='{"amount":10}',
    )
    agent = SimpleNamespace(name="Support agent")
    tool = SimpleNamespace(name="refund")
    asyncio.run(hooks.on_tool_start(context, agent, tool))
    asyncio.run(hooks.on_tool_end(context, agent, tool, {"status": "ok"}))
    x.flush()

    assert [e["event_type"] for e in c.events] == ["tool_requested", "tool_executed"]
    assert c.events[0]["payload"]["call_id"] == "call_123"
    assert c.events[1]["payload"]["status"] == "completed"
    assert not any(e["event_type"] == "outcome_observed" for e in c.events)


def test_run_hooks_default_to_commitments_not_raw_content():
    import asyncio

    x, c = integration()
    hooks = x.run_hooks("dec_1")
    context = SimpleNamespace(
        tool_name="refund",
        tool_call_id="call_secret",
        tool_arguments='{"secret":"do-not-store"}',
    )
    asyncio.run(hooks.on_tool_start(context, SimpleNamespace(name="agent"), SimpleNamespace(name="refund")))
    asyncio.run(hooks.on_tool_end(context, SimpleNamespace(name="agent"), SimpleNamespace(name="refund"), {"secret": "also-do-not-store"}))
    x.flush()
    assert "input" not in c.events[0]["payload"]
    assert "output" not in c.events[1]["payload"]
    assert "do-not-store" not in str(c.events)


def test_human_review_is_explicit():
    x, c = integration()
    x.record_human_review("dec_1", reviewer="reviewer-1", approved=True, reason="checked")
    assert c.events[-1]["event_type"] == "human_approved"
    assert c.events[-1]["payload"]["reviewer"] == "reviewer-1"


def test_human_rejection_is_explicit():
    x, c = integration()
    x.record_human_review("dec_1", reviewer="reviewer-1", approved=False)
    assert c.events[-1]["event_type"] == "human_rejected"


def test_outcome_is_explicit_only():
    x, c = integration()
    x.record_outcome("dec_1", {"status": "succeeded"}, observer="billing-webhook")
    assert c.events[-1]["event_type"] == "outcome_observed"
    assert c.events[-1]["actor_id"] == "billing-webhook"


def test_unbound_traces_are_ignored():
    x, c = integration()
    x.processor.on_span_end(span("generation"))
    x.flush()
    assert c.events == []


def test_trace_mapping_isolated_under_concurrency():
    x, c = integration()
    x.processor.on_trace_start(trace("trace_a", "dec_a"))
    x.processor.on_trace_start(trace("trace_b", "dec_b"))
    x.processor.on_span_end(span("generation", trace_id="trace_b", span_id="b"))
    x.processor.on_span_end(span("generation", trace_id="trace_a", span_id="a"))
    x.flush()
    assert {e["decision_id"] for e in c.events} == {"dec_a", "dec_b"}


def test_idempotency_is_deterministic_for_same_span_identity():
    x, c = integration()
    x.processor.on_trace_start(trace())
    s = span("generation", span_id="same")
    x.processor.on_span_end(s); x.processor.on_span_end(s)
    x.flush()
    assert c.events[0]["idempotency_key"] == c.events[1]["idempotency_key"]


def test_transport_failures_are_observable():
    x, c = integration()
    c.raise_events = True
    x.processor.on_trace_start(trace())
    x.processor.on_span_end(span("generation"))
    x.flush()
    assert x.errors
    with pytest.raises(RuntimeError, match="transport failed"):
        x.assert_healthy()
