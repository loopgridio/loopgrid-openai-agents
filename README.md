# LoopGrid for OpenAI Agents SDK

Native OpenAI Agents SDK tracing + lifecycle integration for LoopGrid, the evidence plane for AI agents.

`loopgrid-openai-agents` maps the Agents SDK native tracing and run lifecycle into LoopGrid decision evidence without changing the LoopGrid Core contract.

## Contract

```text
application starts consequential decision
        ↓
OpenAI Agents SDK GenerationSpan end
        ↓
LoopGrid model_completed
        ↓
explicit application policy / human review
        ↓
OpenAI Agents SDK RunHooks.on_tool_start
        ↓
LoopGrid tool_requested
        ↓
actual local tool invocation
        ↓
OpenAI Agents SDK RunHooks.on_tool_end
        ↓
LoopGrid tool_executed
        ↓
application explicitly observes downstream outcome
        ↓
LoopGrid outcome_observed
        ↓
evidence_complete → cryptographic verification
```

LoopGrid **does not** execute tools, invent delegated authority, invent policy, invent a reviewer, or infer a real-world outcome from an SDK tool return.

## Compatibility

- Python 3.10+
- `openai-agents` 0.23.1.x
- `loopgrid` 0.8.x
- LoopGrid Core validation target: `0.8.1-design-partner`

## Install

```bash
pip install loopgrid-openai-agents
```

## Quickstart

```python
from agents import Agent, RunConfig, Runner
from loopgrid_openai_agents import LoopGridOpenAIAgents

loopgrid = LoopGridOpenAIAgents(
    base_url="http://127.0.0.1:8000",
    workspace_id="default",
    agent_id="support-agent",
).install()

decision = loopgrid.start_decision(
    decision_type="customer_refund",
    agent={"id": "support-agent", "version": "1"},
    authority={"acting_for": "Example Store", "scope": ["refund:create"], "limit_usd": 100},
    model={"provider": "openai", "name": "gpt-5"},
    context={"prompt_version": "support-v1"},
    proposed_action={"tool": "refund.create", "amount": 25, "currency": "USD"},
    policy={
        "policy_id": "refund-policy",
        "version": "1",
        "decision": "auto_allowed",
        "reason": "Within delegated threshold",
    },
)

agent = Agent(name="Support agent", instructions="Handle the request", tools=[...])
hooks = loopgrid.run_hooks(decision["decision_id"])

result = Runner.run_sync(
    agent,
    "Handle this duplicate charge.",
    run_config=RunConfig(
        trace_metadata=loopgrid.trace_metadata(decision["decision_id"]),
        # Recommended: let the Agents SDK redact model/tool content at its tracing boundary too.
        trace_include_sensitive_data=False,
    ),
    hooks=hooks,
)

loopgrid.flush()
loopgrid.assert_healthy()

# Only after the application observes the real downstream business result:
loopgrid.record_outcome(
    decision["decision_id"],
    {"status": "succeeded", "external_reference": "refund_123"},
)
```

## Human approval

The OpenAI Agents SDK remains the approval mechanism. LoopGrid records evidence about an application-authenticated reviewer; it does not replace `needs_approval`, `RunState.approve()`, guardrails, or application authorization.

Tool evidence is deliberately taken from `RunHooks.on_tool_start` / `on_tool_end`, not from `FunctionSpan` start/end. OpenAI Agents can create a function span when a `needs_approval` call pauses before execution; the run hooks bracket the actual local tool invocation, so a pending approval cannot be misreported as execution evidence.

```python
hooks = loopgrid.run_hooks(decision_id)
result = Runner.run_sync(agent, prompt, run_config=run_config, hooks=hooks)

if result.interruptions:
    state = result.to_state()
    for interruption in result.interruptions:
        # Authenticate and authorize this identity in YOUR application.
        reviewer = "reviewer@example.com"
        loopgrid.record_human_review(
            decision_id,
            reviewer=reviewer,
            approved=True,
            reason="Reviewed refund evidence",
        )
        state.approve(interruption)

    result = Runner.run_sync(agent, state, run_config=run_config, hooks=hooks)
```

For durable approvals, keep the full serialized `RunState` in trusted server-side storage. Do not accept a client-supplied replacement state as authoritative.

## Native integration surface

The integration uses two native Agents SDK surfaces for different evidence boundaries:

- `TracingProcessor` + trace metadata for model-completion evidence.
- decision-scoped `RunHooks` for actual local tool invocation boundaries.

Transport work is queued to a background worker so tracing and run-hook callbacks return quickly.

Decision association is explicit and concurrency-safe:

```python
run_config = RunConfig(trace_metadata=loopgrid.trace_metadata(decision_id))
hooks = loopgrid.run_hooks(decision_id)
```

There is no process-global mutable "current decision". A resumed approval flow can recreate `run_hooks(decision_id)` in the process that actually resumes the run.

## Privacy

LoopGrid defaults to `capture_content=False`.

Model outputs and tool inputs/outputs are represented by SHA-256 commitments rather than raw content. For defense in depth, also use:

```python
RunConfig(trace_include_sensitive_data=False)
```

The OpenAI Agents SDK documentation notes that tracing can contain model and function inputs/outputs unless sensitive-data tracing is disabled.

## Failure behavior

Tracing and run-hook callbacks never raise into the Agents SDK execution path. LoopGrid transport failures are retained and surfaced explicitly:

```python
loopgrid.flush()
loopgrid.assert_healthy()
```

A successful agent run is not evidence that LoopGrid transport succeeded unless `assert_healthy()` passes.

## Validation status

v0.1.0 is **publicly released and validated**. Release validation and rollout status:

- exact `openai-agents==0.23.1` runtime install
- semantic/unit tests
- deterministic `ScriptedModel` runtime test through the real Runner/tool pipeline
- real LoopGrid Core E2E
- real human approval/pause/resume E2E
- `evidence_complete`, 100% applicable coverage, `verify.valid=true`, no failures
- wheel/sdist build + `twine check`
- fresh wheel install/import test
- GitHub CI, tag, release
- PyPI Trusted Publishing + fresh public install
- website/docs only after public install
- upstream ecosystem listing only after all technical gates pass

## License

Apache-2.0.
