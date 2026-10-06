# Release process

Do not publish RC2.

Release only after every technical gate in `VALIDATION.md` passes.

Target identities:
- GitHub: `loopgridio/loopgrid-openai-agents`
- PyPI: `loopgrid-openai-agents`
- first release: `v0.1.0`
- PyPI GitHub environment: `pypi`

Release order:
1. local real dependency/runtime validation
2. real LoopGrid Core E2E
3. real human approval/resume E2E
4. build/twine/fresh-wheel install
5. GitHub + CI
6. tag exact tested commit + GitHub Release
7. PyPI Trusted Publishing
8. fresh public PyPI install
9. LoopGrid website/docs
10. upstream ecosystem submission
