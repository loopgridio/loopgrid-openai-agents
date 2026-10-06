# Changelog

## 0.1.0 - unreleased

- Native OpenAI Agents SDK `TracingProcessor` integration.
- Decision-scoped trace correlation.
- Model evidence from native tracing plus local-tool evidence from decision-scoped native `RunHooks`, with privacy-safe commitments.
- Explicit policy, human review, and observed-outcome helpers.

- RC2 corrects approval semantics: pre-approval `FunctionSpan` callbacks are never treated as tool execution evidence.
