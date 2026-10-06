# RC2 validation plan

## Completed in repository construction

- [x] Current OpenAI Agents SDK release researched (`0.23.1`, 2026-10-02)
- [x] Native `TracingProcessor` selected for model evidence
- [x] Native decision-scoped `RunHooks` selected for actual local tool invocation evidence
- [x] Native human approval flow reviewed (`interruptions` + `RunState.approve/reject`)
- [x] Same LoopGrid evidence contract preserved
- [x] Decision-scoped correlation via trace metadata; no global current-decision variable
- [x] Privacy-safe commitments by default
- [x] Background transport worker so tracing callbacks do not perform blocking HTTP
- [x] Explicit transport health surface (`flush` + `assert_healthy`)

## Required before release

- [ ] install exact `openai-agents==0.23.1`
- [ ] run unit/semantic tests with real dependencies
- [ ] Python compile check
- [ ] deterministic `ScriptedModel` Runner + real function-tool runtime test
- [ ] real LoopGrid Core `0.8.1-design-partner` auto-allowed E2E
- [ ] require `evidence_complete`
- [ ] require applicable coverage `100%`
- [ ] require `verify.valid=true` and `failures=[]`
- [ ] real `needs_approval=True` interruption + `RunState.approve` resume E2E
- [ ] verify reviewer evidence precedes execution evidence
- [ ] build wheel/sdist
- [ ] `twine check dist/*`
- [ ] inspect wheel contents
- [ ] fresh venv install built wheel
- [ ] public import test
- [ ] GitHub Actions Python matrix
- [ ] exact tested commit tagged `v0.1.0`
- [ ] PyPI Trusted Publishing
- [ ] fresh public PyPI install test
- [ ] website/docs integration page
- [ ] OpenAI Agents SDK ecosystem submission only after public validation
