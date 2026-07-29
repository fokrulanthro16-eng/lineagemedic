# Demo video shot list

A three-minute recording plan for the hackathon submission. Every shot below is
something the running system actually does — each was performed live while
capturing the screenshots in `docs/screenshots/`.

No video has been recorded yet. When one exists at a public URL, add the link to
`README.md` and to `docs/DEVPOST_SUBMISSION.md`; until then neither file claims
a video exists.

## Before recording

```powershell
# Live mode, so the DataHub shots are real.
docker compose -p datahub `
  -f $HOME\.datahub\quickstart\docker-compose.yml `
  -f docker\datahub-quickstart.override.yml up -d

.venv-datahub\Scripts\python.exe scripts\ingest_lineage.py

$env:LINEAGEMEDIC_MODE = "live"
$env:DATAHUB_GMS_URL   = "http://localhost:8080"
.\scripts\start.ps1
```

Confirm `Invoke-RestMethod http://localhost:8000/status/integrations` reports
`datahub_connected: true` before you hit record. Sign in to
<http://localhost:9002> in a second tab ahead of time so no credential entry is
filmed.

Record at 1920x1080 or wider. The DataHub lineage graph needs roughly 2200px of
viewport to show the full downstream fan-out without scrolling.

## Shot list

| # | Duration | Screen | What to show | What to say |
|---|----------|--------|--------------|-------------|
| 1 | 0:00-0:20 | Dashboard, idle | The three scenarios and the live-mode header | "Silent data failures don't page anyone. A column quietly goes bad upstream and a production model keeps serving predictions from it." |
| 2 | 0:20-0:35 | Click the critical scenario | The agent steps running in order | "Seven agents run in sequence against a real DataHub catalog — quality, context, impact, root cause, remediation, safety." |
| 3 | 0:35-1:00 | Verdict panel | `CRITICAL`, the incident ID, 80% confidence | "The severity is derived, not declared: five checks failed and the blast radius reaches a deployed model and a serving endpoint. Nothing in the code hardcodes 'critical'." |
| 4 | 1:00-1:20 | Impact panel | Affected vs cleared assets | "The billing branch shares an upstream ancestor with the failing patient branch — and it is correctly left in service. Blast radius here is selective, not everything-downstream." |
| 5 | 1:20-1:35 | Root cause panel | `raw_patients`, columns `admission_date`, `age` | "It walks back up the lineage to the origin column, not just the table where the symptom showed up." |
| 6 | 1:35-2:00 | Remediation and approval | The plan, then click **Approve plan** | "Every action is labelled safe and reversible with an explicit rollback. Nothing is written to the catalog until a human approves — enforced at three independent layers." |
| 7 | 2:00-2:15 | Writeback receipt | The green APPLIED receipt naming the aspects | "The writeback names the incident, the asset count, and the exact aspects it wrote." |
| 8 | 2:15-2:45 | **Switch to DataHub** at `:9002` | `model_predictions` → Documentation tab and Tags | "Independent verification, in DataHub's own UI rather than ours: the incident note and both tags are really there. Every write is confirmed by reading the metadata back." |
| 9 | 2:45-3:00 | DataHub Lineage tab for `train_readmission_model` | The upstream and three downstream entities | "And this is the real graph the blast radius walked." |

## Optional closing shot

If time allows, run the healthy control scenario and show it deriving `HEALTHY`
with no root cause attributed. It is the strongest single answer to "is this
just always saying critical?"

## Do not film

- The DataHub login screen or any credential entry.
- Terminal windows showing filesystem paths under a personal home directory.
- Any browser tab other than `localhost:5173` and `localhost:9002`.
