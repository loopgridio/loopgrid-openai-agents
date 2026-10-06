# Security

- Raw model/tool content is disabled by default (`capture_content=False`).
- Use `RunConfig(trace_include_sensitive_data=False)` unless raw SDK trace content is intentionally required.
- Reviewer identity must come from authenticated application state, not model output or browser-submitted fields.
- Durable OpenAI Agents `RunState` snapshots must remain under trusted server-side control.
- LoopGrid evidence verification proves integrity/provenance of recorded evidence; it does not prove that a business decision was correct.
- Report security issues privately through the LoopGrid security contact/process rather than a public issue.
