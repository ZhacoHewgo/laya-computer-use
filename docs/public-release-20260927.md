# Public release check — 2026-09-27

Scope: publish the current browser reliability improvements and evaluation examples.

- Scanned all 155 existing Git-history blobs and all non-ignored working files for common API-key/private-key patterns, literal credential assignments, local user paths and email addresses. No matches in file contents. This is a bounded pattern scan, not a guarantee that every possible secret format is detectable.
- Git commit metadata retains historical author email addresses; history has not been rewritten.
- `.env`, `.venv`, `artifacts` and build outputs are excluded from Git. The repository includes aggregate measurements and selected public-page screenshots, not full local runtime traces.
- MIT license and upstream attribution retained. Model weights are not included; their own terms still apply.
- Offline checks: 190 tests passed, Ruff passed, both JavaScript syntax checks passed, build passed and `git diff --check` passed.
- Live evaluation results and their limitations are recorded in the dated reports, most recently `name-order-20260926.md`. Publication does not turn these limited samples into a general browser-task success rate.
- These changes are an experimental DOM-based browser agent, not screenshot-based desktop control. Planner usage can incur provider fees even when its gateway is on localhost.
