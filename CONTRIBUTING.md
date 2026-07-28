# Contributing

## Getting set up

Requires Python 3.11 (`py -3.11`) and Node 20+. No Docker, cloud account, or
API key is needed.

```powershell
.\scripts\setup.ps1
.\scripts\test.ps1
```

If `test.ps1` is green, your environment is correct.

## Before you push

```powershell
.\scripts\test.ps1
```

This runs the five gates CI runs, in the same order: ruff, mypy, pytest, tsc,
vitest. Every gate runs even when an earlier one fails, so one invocation
reports every problem.

If you changed an API response model, also run:

```powershell
.\scripts\check_api_types.ps1
```

and commit the regenerated `scripts/openapi.json` and
`apps/web/src/api/schema.ts`. CI fails if they are stale.

If you changed anything that affects diagnosis output, regenerate the examples:

```powershell
.\.venv\Scripts\python.exe scripts\export_examples.py
```

and commit the result. CI regenerates and diffs them too.

## The rules that matter

This project's value is that its output can be trusted. A few rules protect
that, and a change that breaks one will not be accepted even if the tests pass.

**Never report a success that did not happen.** If an integration is absent,
say so. `skipped_fixture_mode` exists because the alternative — a green
checkmark for a writeback that never occurred — makes the entire tool
worthless. This extends to tests, screenshots, and documentation.

**Provenance is required, not optional.** Anything derived from catalog
metadata carries a `DataSource`. Do not add a code path that produces a
lineage result without recording where it came from.

**Severity is derived from measurements.** A scenario may state what it
expects, but that value must never feed the derivation. If you need a scenario
to come out critical, change the measurements, not the label.

**The LLM cannot reach conclusions.** The narrator rephrases a summary that is
already complete. If a change makes severity, evidence, root cause, or the plan
depend on a model being available, it will be rejected.

**Approval precedes mutation.** The gate exists in the Safety agent, the
Writeback agent, and the HTTP endpoint. Removing any layer requires a very good
reason.

**No placeholder work.** No empty buttons, no TODO-only features, no metrics
that aren't measured, no documentation for behaviour that does not exist.

## Style

Ruff and mypy are configured in the **root** `pyproject.toml`, and only there.

Do not add `[tool.ruff]` or `[tool.mypy]` tables to
`packages/lineagemedic/pyproject.toml` or `apps/api/pyproject.toml`. Ruff
resolves configuration from the *nearest* pyproject to each file, so a table in
a nested package silently shadows the root configuration for everything beneath
it — which is exactly how the two drifted apart once already.

Backend code is fully typed; `disallow_untyped_defs` is on for everything
except `tests.*`.

Comments should explain *why*, particularly where the code resists an obvious
simplification. A comment restating what the line does is noise.

## Tests

- Backend tests go in `tests/`, using the fixtures in `tests/conftest.py`.
- Frontend tests sit beside their subject in `__tests__/`.
- A test asserting that the tool refuses to do something is as valuable as one
  asserting it succeeds — often more so. The approval gate, the 501 on live
  mode, and the fixture-mode receipt are all covered this way.

## Commits

Explain why the change was needed, not just what changed. If a fix corrects a
subtle failure, describe the failure — that is the part a future reader cannot
reconstruct from the diff.

## Adding a scenario

1. Add it to `packages/lineagemedic/src/lineagemedic/scenarios.py` with an
   `expected_severity`.
2. Make sure the warehouse seed actually contains the condition it describes.
   A scenario that expects a failure the data cannot produce is a fabricated
   scenario.
3. Run `scripts/demo.ps1` and confirm no `MISMATCH` is printed.
4. Regenerate the examples and commit them.
