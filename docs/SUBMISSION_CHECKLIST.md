# Submission checklist

Tracks what is done, what still needs a human, and what must never be claimed
without evidence.

## Repository metadata — needs manual application

The `gh` CLI is not installed in the environment this repository was polished
in, so these could not be applied automatically. Apply them through the GitHub
web UI at **Settings → General** and the **About** panel on the repository home
page.

**Description** (paste verbatim):

```
Diagnose, contain, and heal silent data failures before they break production ML. A seven-agent workflow over a real DataHub catalog: derived severity, selective blast radius, human-approved metadata writeback.
```

**Topics** (add all ten):

```
datahub
data-lineage
data-quality
ai-agents
mlops
healthcare-data
fastapi
react
python
observability
```

**Also set:**

- Website field: leave **empty**. There is no deployed instance, and a dead link
  is worse than none.
- Releases/Packages/Environments in the About panel: unchecked, since none exist.

## Done

- [x] Phase 1 — audit recorded in [`REPO_POLISH_AUDIT.md`](REPO_POLISH_AUDIT.md)
- [x] Phase 2 — seven screenshots captured from the real running app and real
      local DataHub, each visually inspected before committing
- [x] Phase 3 — `README.md` rebuilt, leading with badges, the tagline, and a
      screenshot above the fold
- [x] Phase 4 — [`JUDGE_TEST_GUIDE.md`](JUDGE_TEST_GUIDE.md),
      [`VIDEO_SHOT_LIST.md`](VIDEO_SHOT_LIST.md),
      [`DEVPOST_SUBMISSION.md`](DEVPOST_SUBMISSION.md), this file
- [x] Phase 6 — quality gates re-run after the final code change (below)

## Quality gates — last full run

Run on 2026-07-29, after the final change to `scripts/export_examples.py`:

| Gate | Command | Result |
|------|---------|--------|
| Python tests | `pytest` | 78 passed |
| Frontend tests | `npm test -- --run` | 28 passed, 4 files |
| Lint | `ruff check .` | All checks passed |
| Types | `mypy packages/lineagemedic/src apps/api scripts` | clean, 30 files |
| API contract | `scripts\check_api_types.ps1` | in sync |
| End-to-end demo | `scripts\demo.ps1` | critical / warning / healthy, no `MISMATCH` |
| Example reproducibility | `scripts\export_examples.py` then `git diff examples\` | no diff |

## Screenshot inventory

| File | Captured from | Shows |
|------|---------------|-------|
| `01-lineagemedic-critical-dashboard.png` | `localhost:5173` | Critical verdict, incident ID, confidence, live indicators |
| `02-selective-blast-radius.png` | `localhost:5173` | Billing branch cleared while patient branch is quarantined |
| `03-datahub-downstream-lineage.png` | `localhost:9002` | `train_readmission_model` and its three downstream entities |
| `04-datahub-upstream-lineage.png` | `localhost:9002` | `raw_patients → staging_patients → patient_features` |
| `05-datahub-writeback.png` | `localhost:9002` | Incident note and both tags, read back from DataHub |
| `06-approval-and-receipt.png` | `localhost:5173` | Approval gate at `approved`, applied writeback receipt |
| `07-healthy-control.png` | `localhost:5173` | Healthy scenario derives healthy, no root cause attributed |

## Must not be claimed without evidence

Carried forward as standing constraints on this repository:

- **No demo video URL** anywhere until a real public one exists. The shot list
  in `VIDEO_SHOT_LIST.md` explicitly states none has been recorded.
- **No deployment URL.** Nothing is hosted. The Website field stays empty.
- **No fabricated screenshots**, test counts, benchmarks, users, testimonials,
  or awards. Every number in the README and in `DEVPOST_SUBMISSION.md` came
  from running the command named beside it.
- **No badge that does not resolve.** The README badges point at the real CI
  workflow, the real `LICENSE`, and static version facts.
- **The one un-expressible lineage hop stays documented**, not hidden. See
  `ARCHITECTURE.md` and the "What DataHub v1.6.0 forced" section of the README.

## Known limitations, stated in the README

- The `readmission_risk_model → model_predictions` hop is rejected by DataHub
  v1.6.0 in both directions; the model is connected via
  `trainingJobs`/`downstreamJobs` instead.
- Re-running `scripts/ingest_lineage.py` replaces the `globalTags` aspect
  wholesale, which clears incident tags written by a previous writeback.
  `globalTags` is a whole-aspect replace in DataHub, not an append.
- Fixture mode and live mode report different blast-radius counts, because the
  live catalog contains a richer graph than the bundled fixture.

## Before pushing

- [x] No secrets, tokens, cookies, or personal filesystem paths in tracked files
- [x] All email addresses use the reserved `.example` TLD
- [x] Apache-2.0 `LICENSE` unmodified, 169 lines
- [x] All application code and integration tests preserved
- [x] English only in all repository files and screenshots
