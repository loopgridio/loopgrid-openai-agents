# Integration contract

## Framework boundary

OpenAI Agents SDK owns:
- agent/model execution
- tool invocation
- tool approval and `RunState` resume
- guardrails
- handoffs and orchestration

LoopGrid owns:
- append-only evidence recording
- lifecycle projection
- signed/tamper-evident evidence
- integrity verification

Application owns:
- delegated authority
- policy facts/decisions
- authenticated reviewer identity
- real external side effects
- authoritative observed downstream outcomes

## Native event mapping

| OpenAI Agents SDK | LoopGrid |
|---|---|
| application starts decision | `decision_created` |
| `GenerationSpan` end | `model_completed` |
| explicit app policy | `policy_evaluated` |
| explicit app reviewer decision | `human_approved` / `human_rejected` |
| `RunHooks.on_tool_start` | `tool_requested` |
| actual local tool invocation | application/framework executes the tool |
| `RunHooks.on_tool_end` | `tool_executed` |
| explicit downstream observation | `outcome_observed` |

The tracing processor and run hooks are observational. They are not an enforcement boundary.

`FunctionSpan` is not used as the local-tool execution boundary because approval-gated calls can create a function span while paused before invocation. The run-hook boundary is intentionally chosen to avoid false execution evidence.
