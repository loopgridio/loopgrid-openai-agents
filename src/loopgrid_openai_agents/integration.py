from __future__ import annotations

import hashlib
import json
import queue
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from agents import RunHooks, TracingProcessor, add_trace_processor
from loopgrid import LoopGrid

LOOPGRID_OPENAI_AGENTS_VERSION = "0.1.0"
OPENAI_AGENTS_TARGET_VERSION = "0.23.1"
_FRAMEWORK = "openai-agents"
_RESERVED_TRACE_KEYS = {
    "loopgrid_decision_id",
    "loopgrid_integration",
    "loopgrid_integration_version",
}


def _require_nonempty(name: str, value: Any) -> None:
    if value is None or value == "" or value == {} or value == []:
        raise ValueError(f"{name} is required")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    except Exception:
        return repr(value).encode("utf-8", errors="replace")


def _commitment(value: Any) -> dict[str, Any]:
    raw = _canonical_bytes(value)
    return {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }


def _idempotency(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts).encode("utf-8")
    return "lg-oai-" + hashlib.sha256(raw).hexdigest()


def _span_error(span: Any) -> Any:
    return getattr(span, "error", None)


def _span_data(span: Any) -> Any:
    return getattr(span, "span_data", None)


def _span_type(span: Any) -> str | None:
    data = _span_data(span)
    return getattr(data, "type", None)


def _span_identity(span: Any) -> tuple[str | None, str | None, str | None]:
    return (
        getattr(span, "trace_id", None),
        getattr(span, "span_id", None),
        getattr(span, "parent_id", None),
    )


def _safe_export(data: Any) -> dict[str, Any]:
    export = getattr(data, "export", None)
    if callable(export):
        try:
            result = export()
            return result if isinstance(result, dict) else {"value": result}
        except Exception:
            pass
    return {"type": getattr(data, "type", type(data).__name__)}


@dataclass(frozen=True)
class _QueuedEvent:
    decision_id: str
    event_type: str
    payload: dict[str, Any]
    actor_type: str
    actor_id: str
    idempotency_key: str


class LoopGridTracingProcessor(TracingProcessor):
    """Native OpenAI Agents SDK tracing processor for LoopGrid.

    The processor is observational. It does not authorize or block tools. OpenAI Agents SDK
    approval/guardrail mechanisms and application policy remain the execution controls.
    """

    def __init__(self, owner: "LoopGridOpenAIAgents") -> None:
        self._owner = owner
        self._trace_to_decision: dict[str, str] = {}
        self._trace_lock = threading.RLock()
        self._queue: queue.Queue[_QueuedEvent | object] = queue.Queue()
        self._stop = object()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_main,
                name="loopgrid-openai-agents",
                daemon=True,
            )
            self._worker.start()

    def _worker_main(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._stop:
                    return
                assert isinstance(item, _QueuedEvent)
                try:
                    self._owner.client.add_event(
                        item.decision_id,
                        item.event_type,
                        item.payload,
                        actor_type=item.actor_type,
                        actor_id=item.actor_id,
                        idempotency_key=item.idempotency_key,
                    )
                except Exception as exc:  # never disrupt the Agents SDK callback thread
                    self._owner._record_error(item.event_type, item.decision_id, exc)
            finally:
                self._queue.task_done()

    def _decision_for_trace(self, trace_id: str | None) -> str | None:
        if not trace_id:
            return None
        with self._trace_lock:
            return self._trace_to_decision.get(trace_id)

    def _enqueue(
        self,
        decision_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor_type: str,
        actor_id: str,
        identity: tuple[Any, ...],
    ) -> None:
        self._ensure_worker()
        self._queue.put(
            _QueuedEvent(
                decision_id=decision_id,
                event_type=event_type,
                payload=payload,
                actor_type=actor_type,
                actor_id=actor_id,
                idempotency_key=_idempotency(decision_id, event_type, *identity),
            )
        )

    def on_trace_start(self, trace: Any) -> None:
        try:
            metadata = getattr(trace, "metadata", None) or {}
            decision_id = metadata.get("loopgrid_decision_id") if isinstance(metadata, Mapping) else None
            trace_id = getattr(trace, "trace_id", None)
            if decision_id and trace_id:
                with self._trace_lock:
                    self._trace_to_decision[str(trace_id)] = str(decision_id)
        except Exception as exc:
            self._owner._record_error("trace_start", None, exc)

    def on_trace_end(self, trace: Any) -> None:
        try:
            trace_id = getattr(trace, "trace_id", None)
            if trace_id:
                with self._trace_lock:
                    self._trace_to_decision.pop(str(trace_id), None)
        except Exception as exc:
            self._owner._record_error("trace_end", None, exc)

    def on_span_start(self, span: Any) -> None:
        # Deliberately no tool evidence here. OpenAI Agents can emit a function span when
        # an approval-gated call pauses *before* the tool is invoked. Actual local tool
        # invocation boundaries are recorded by LoopGridRunHooks.on_tool_start/on_tool_end.
        return

    def on_span_end(self, span: Any) -> None:
        try:
            trace_id, span_id, parent_id = _span_identity(span)
            decision_id = self._decision_for_trace(trace_id)
            if not decision_id:
                return
            span_type = _span_type(span)
            data = _span_data(span)

            if span_type == "generation":
                model = getattr(data, "model", None) or self._owner.default_model_name
                raw_output = getattr(data, "output", None)
                usage = getattr(data, "usage", None)
                model_config = getattr(data, "model_config", None)
                payload: dict[str, Any] = {
                    "framework": _FRAMEWORK,
                    "provider": "openai",
                    "model": model,
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_id": parent_id,
                    "output_commitment": _commitment(raw_output),
                    "usage": usage,
                    "model_config_commitment": _commitment(model_config),
                }
                if self._owner.capture_content:
                    payload["output"] = raw_output
                    payload["model_config"] = model_config
                self._enqueue(
                    decision_id,
                    "model_completed",
                    payload,
                    actor_type="agent",
                    actor_id=self._owner.agent_id,
                    identity=(trace_id, span_id, "end"),
                )
                return

            # Function spans are intentionally ignored here. They can represent an
            # approval pause rather than an invocation. Local tool evidence comes from
            # RunHooks, whose callbacks bracket the actual invocation.
            return
        except Exception as exc:
            self._owner._record_error("span_end", None, exc)

    def force_flush(self) -> None:
        self._ensure_worker()
        self._queue.join()

    def shutdown(self) -> None:
        self.force_flush()
        worker = self._worker
        if worker is not None and worker.is_alive():
            self._queue.put(self._stop)
            self._queue.join()
            worker.join(timeout=5)


class LoopGridRunHooks(RunHooks):
    """Decision-scoped OpenAI Agents run hooks for actual local tool invocation evidence.

    `on_tool_start`/`on_tool_end` are native Agents SDK lifecycle boundaries around the local
    tool invocation. In particular, they do not fire merely because a `needs_approval` call
    has paused for human approval.
    """

    def __init__(self, owner: "LoopGridOpenAIAgents", decision_id: str) -> None:
        self._owner = owner
        self._decision_id = decision_id

    @staticmethod
    def _tool_context(context: Any, tool: Any) -> tuple[str, str | None, Any]:
        name = getattr(context, "tool_name", None) or getattr(tool, "name", None) or "local_tool"
        call_id = getattr(context, "tool_call_id", None)
        arguments = getattr(context, "tool_arguments", None)
        return str(name), str(call_id) if call_id else None, arguments

    def _identity(self, *, name: str, call_id: str | None, arguments: Any, phase: str) -> tuple[Any, ...]:
        # Function tools expose a stable model tool-call id. For other local tool families,
        # use a deterministic content-derived fallback rather than a process-global counter.
        stable_call = call_id or f"local-{name}-{_commitment(arguments)['sha256']}"
        return ("run_hook", stable_call, phase)

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        try:
            name, call_id, arguments = self._tool_context(context, tool)
            payload: dict[str, Any] = {
                "framework": _FRAMEWORK,
                "tool": name,
                "agent": getattr(agent, "name", None),
                "call_id": call_id,
                "input_commitment": _commitment(arguments),
                "source": "run_hooks",
            }
            if self._owner.capture_content:
                payload["input"] = arguments
            self._owner.processor._enqueue(
                self._decision_id,
                "tool_requested",
                payload,
                actor_type="tool",
                actor_id=name,
                identity=self._identity(name=name, call_id=call_id, arguments=arguments, phase="start"),
            )
        except Exception as exc:
            self._owner._record_error("tool_start", self._decision_id, exc)

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: object) -> None:
        try:
            name, call_id, arguments = self._tool_context(context, tool)
            payload: dict[str, Any] = {
                "framework": _FRAMEWORK,
                "tool": name,
                "agent": getattr(agent, "name", None),
                "call_id": call_id,
                "status": "completed",
                "output_commitment": _commitment(result),
                "source": "run_hooks",
            }
            if self._owner.capture_content:
                payload["output"] = result
            self._owner.processor._enqueue(
                self._decision_id,
                "tool_executed",
                payload,
                actor_type="tool",
                actor_id=name,
                identity=self._identity(name=name, call_id=call_id, arguments=arguments, phase="end"),
            )
        except Exception as exc:
            self._owner._record_error("tool_end", self._decision_id, exc)


class LoopGridOpenAIAgents:
    """LoopGrid integration for the OpenAI Agents SDK tracing lifecycle."""

    def __init__(
        self,
        *,
        client: LoopGrid | None = None,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        workspace_id: str = "default",
        agent_id: str = "openai-agent",
        capture_content: bool = False,
        default_model_name: str | None = None,
    ) -> None:
        _require_nonempty("agent_id", agent_id)
        self.client = client or LoopGrid(base_url=base_url, api_key=api_key, workspace_id=workspace_id)
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.capture_content = bool(capture_content)
        self.default_model_name = default_model_name
        self.processor = LoopGridTracingProcessor(self)
        self._errors: list[dict[str, Any]] = []
        self._errors_lock = threading.Lock()
        self._installed = False

    def install(self) -> "LoopGridOpenAIAgents":
        if not self._installed:
            add_trace_processor(self.processor)
            self._installed = True
        return self

    def trace_metadata(self, decision_id: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        _require_nonempty("decision_id", decision_id)
        extra_dict = dict(extra or {})
        conflicts = _RESERVED_TRACE_KEYS.intersection(extra_dict)
        if conflicts:
            raise ValueError(f"trace metadata cannot override reserved LoopGrid keys: {sorted(conflicts)}")
        return {
            **extra_dict,
            "loopgrid_decision_id": decision_id,
            "loopgrid_integration": _FRAMEWORK,
            "loopgrid_integration_version": LOOPGRID_OPENAI_AGENTS_VERSION,
        }

    def run_hooks(self, decision_id: str) -> LoopGridRunHooks:
        """Create native, decision-scoped RunHooks for actual local tool invocations."""
        _require_nonempty("decision_id", decision_id)
        return LoopGridRunHooks(self, decision_id)

    def start_decision(
        self,
        *,
        decision_type: str,
        agent: Mapping[str, Any],
        authority: Mapping[str, Any],
        model: Mapping[str, Any],
        context: Mapping[str, Any],
        proposed_action: Mapping[str, Any],
        policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        privacy_mode: str = "redacted",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        _require_nonempty("decision_type", decision_type)
        _require_nonempty("agent.id", dict(agent).get("id"))
        _require_nonempty("authority", authority)
        _require_nonempty("model.name", dict(model).get("name"))
        _require_nonempty("context", context)
        _require_nonempty("proposed_action", proposed_action)

        md = dict(metadata or {})
        md.update(
            {
                "framework": _FRAMEWORK,
                "integration": "loopgrid-openai-agents",
                "integration_version": LOOPGRID_OPENAI_AGENTS_VERSION,
                "openai_agents_target": OPENAI_AGENTS_TARGET_VERSION,
            }
        )
        result = self.client.record_decision(
            decision_type=decision_type,
            agent=dict(agent),
            authority=dict(authority),
            model=dict(model),
            context=dict(context),
            proposed_action=dict(proposed_action),
            metadata=md,
            privacy_mode=privacy_mode,
            idempotency_key=idempotency_key,
        )
        decision_id = result["decision_id"]
        if policy is not None:
            self.record_policy(decision_id, policy)
        return result

    def record_policy(self, decision_id: str, policy: Mapping[str, Any]) -> Any:
        p = dict(policy)
        decision = p.get("decision")
        if decision not in {"auto_allowed", "human_approval_required", "blocked", "block"}:
            raise ValueError("policy.decision must be auto_allowed, human_approval_required, blocked, or block")
        if not (p.get("policy_id") or p.get("version")):
            raise ValueError("policy provenance requires policy_id or version")
        return self.client.policy_evaluated(decision_id, p, actor_id=str(p.get("policy_id") or "application-policy"))

    def record_human_review(
        self,
        decision_id: str,
        *,
        reviewer: str,
        approved: bool,
        reason: str = "",
    ) -> Any:
        _require_nonempty("reviewer", reviewer)
        if approved:
            return self.client.human_approved(decision_id, reviewer, reason)
        return self.client.human_rejected(decision_id, reviewer, reason)

    def record_outcome(self, decision_id: str, outcome: Mapping[str, Any], *, observer: str = "application") -> Any:
        _require_nonempty("outcome", outcome)
        return self.client.outcome_observed(decision_id, dict(outcome), actor_id=observer)

    def flush(self) -> None:
        self.processor.force_flush()

    def shutdown(self) -> None:
        self.processor.shutdown()

    def _record_error(self, stage: str, decision_id: str | None, exc: BaseException) -> None:
        with self._errors_lock:
            self._errors.append(
                {
                    "stage": stage,
                    "decision_id": decision_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )

    @property
    def errors(self) -> tuple[dict[str, Any], ...]:
        with self._errors_lock:
            return tuple(dict(x) for x in self._errors)

    def assert_healthy(self) -> None:
        errors = self.errors
        if errors:
            first = errors[0]
            raise RuntimeError(
                f"LoopGrid tracing transport failed ({len(errors)} error(s)); "
                f"first={first['stage']}:{first['error_type']}"
            )
