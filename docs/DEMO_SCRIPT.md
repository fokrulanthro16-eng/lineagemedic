# Demo script

A three-minute walkthrough. Every number shown comes from a live HTTP response;
nothing in the demo path is hardcoded, so a regression changes the output rather
than quietly staying pretty.

## Setup (before recording)

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

Confirm the backend answers before starting:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Then open <http://localhost:5173>.

---

## 0:00 — The problem

> "This is a healthcare readmission model in production. Nothing is on fire.
> No pipeline failed, no alert fired. But the model has been scoring patients
> using a column that started carrying impossible values three days ago, and
> nobody knows yet."

Point at the banner across the top of the dashboard:

> "Note this first: **Demo Fixture Mode — DataHub integration not connected.**
> This build runs against committed fixtures. The tool says so on every screen,
> in every API response, and in the writeback receipt. I'd rather show you a
> tool that admits what it doesn't have than one that fakes a connection."

## 0:25 — Diagnose the critical incident

Select **Invalid patient ages reaching the production readmission model** and
run the diagnosis.

> "Seven agents run in order. Quality measures the warehouse. Context pulls
> schema, owners, and lineage from the catalog. Impact walks downstream. Root
> Cause ranks hypotheses. Remediation proposes a plan. Safety decides what
> needs a human. Writeback records it — if, and only if, a human approves."

Point at the severity chip.

> "Critical. And critical isn't something the scenario declared — the scenario
> doesn't get a vote. Severity is derived: five checks failed, and the blast
> radius reaches a deployed model and a production endpoint. That combination
> is what makes it critical."

## 0:55 — The evidence

Open the evidence panel.

> "Every claim is backed by a measurement. The age check expects at most 1% of
> rows out of range; it observed 7.4%, which is 37 of 500 rows scanned. That's
> not a severity label someone typed in — it's the number the check returned,
> with its threshold next to it so you can judge it yourself."

## 1:20 — Blast radius, and what is *not* affected

Point at the lineage graph.

> "Five assets are contaminated: raw patients, staging, the feature table, the
> model, and the production endpoint. Left to right is the actual dependency
> order — a node always sits to the right of everything it depends on."

Now point at the cleared assets.

> "This is the part I care about most. Two assets were examined and **cleared**.
> The billing branch shares an upstream ancestor with the failing assets, so a
> naive blast radius would flag it. It's still in service, because the defect
> is in a column billing doesn't consume. A tool that flags everything during
> an incident isn't diagnosing — it's panicking."

## 1:45 — Root cause and remediation

> "The defect entered at `raw_patients`, in the age and admission_date columns.
> Confidence is 80%, and the tool explains where that number comes from rather
> than asserting it: lineage structure, two independent failed checks on the
> suspected asset, no gaps in the resolved graph — and it deducts confidence
> because context came from fixtures rather than a live DataHub."

Show the remediation plan.

> "An ordered plan, each step with a rollback."

## 2:10 — The approval gate

Attempt the writeback **before** approving.

> "Refused. HTTP 403."

This is also shown by `scripts/demo.ps1`, which attempts exactly this:

```
  Attempting a writeback BEFORE approval (expected to be refused)...
    refused with HTTP 403 - the approval gate holds.
```

> "That gate exists in three independent places: the Safety agent, the Writeback
> agent, and the HTTP endpoint. Each is separately tested, because a gate that
> lives in only one layer is a gate a refactor can delete."

Now approve, then run the writeback.

> "Approved — and here's the receipt. It says **`skipped_fixture_mode`**: *no
> writeback performed, fixture mode*. There's no DataHub attached, so nothing
> was written, and the tool tells you that instead of showing a green
> checkmark. When the live adapter is connected, this same receipt reports what
> was actually emitted."

## 2:35 — The healthy control

Run **Billing branch control check**.

> "Healthy. Same engine, same checks, no findings. The tool is capable of
> saying nothing is wrong — which is what makes it worth believing when it says
> something is."

## 2:50 — Close

> "Deterministic diagnosis, evidence for every claim, a human gate before any
> mutation, and an honest report of what it is and isn't connected to. The
> language model is optional and only rewords the summary — pull it out and the
> diagnosis is byte-identical."

---

## Terminal-only variant

If the browser is unavailable, `scripts/demo.ps1` covers the same ground:

```powershell
.\scripts\demo.ps1
```

It prints integration status, runs all three scenarios, compares each derived
severity against the scenario's expectation (printing `MISMATCH` on divergence),
then walks the critical incident through refusal → approval → receipt.

## If something goes wrong

| Symptom | Action |
|---------|--------|
| Dashboard shows a connection error | Backend is not running. `.\scripts\start.ps1 -ApiOnly`, then check `logs\api.err.log`. |
| `database_present: false` | `.\venv\Scripts\python.exe scripts\seed_warehouse.py` |
| Port already in use | `.\scripts\stop.ps1`, then start again. |
| A severity looks wrong | Do not explain it away. `.\scripts\demo.ps1` prints `MISMATCH` if derivation diverged from expectation — that is a real bug, not a demo glitch. |
