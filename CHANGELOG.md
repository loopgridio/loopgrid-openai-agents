# Changelog

## 0.1.0 - 2026-10-06

- Native OpenAI Agents SDK `TracingProcessor` integration.
- Decision-scoped trace correlation.
- Model evidence from native tracing plus local-tool evidence from decision-scoped native `RunHooks`, with privacy-safe commitments.
- Explicit policy, human review, and observed-outcome helpers.

- Approval semantics finalized before public release: pre-approval `FunctionSpan` callbacks are never treated as tool execution evidence.
