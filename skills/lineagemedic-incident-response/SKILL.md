---
name: lineagemedic-incident-response
description: Diagnose a silent data quality failure and assess its blast radius through lineage. Use when a data or ML asset is suspected of carrying bad values, when a model's inputs may be contaminated, or when you need to determine which downstream assets to contain and which can safely stay in service.
---

# Incident response for silent data failures

A silent data failure produces no alert. The pipeline succeeds, the job is
green, and the model keeps serving predictions from values that are wrong. The
useful question is rarely "did something break" — it is **"what has this been
contaminating, and what can I leave alone?"**

This skill runs that investigation in a fixed order and refuses to skip steps.

## Core discipline

**Measure before concluding.** Every claim you make must be traceable to a
value you actually observed. "The age column looks wrong" is not a finding.
"37 of 500 rows are outside 0–130, against a 1% threshold" is.

**Separate what is affected from what is merely downstream.** Sharing an
upstream ancestor with a broken asset does not make an asset broken. If the
defect is in a column an asset does not consume, that asset stays in service
and you say so explicitly. Flagging everything during an incident is not
diagnosis.

**Never report an action you did not take.** If a catalog write was not
performed — because there was no approval, no connection, or no permission —
say that plainly. A success reported for an action that did not happen is worse
than no report at all.

**Do not let a language model reach the conclusion.** Use it to phrase findings
if useful. Severity, evidence, root cause, and the remediation plan must be
derivable from measurements without it.

## Procedure

### 1. Measure

Run quality checks against the suspected asset. For each check record: what was
expected, what was observed, how many rows were scanned, and how many failed.
Keep the threshold beside the observation — a number without its threshold is
not evidence.

### 2. Gather context

Retrieve schema, ownership, and lineage for the affected assets from the
catalog. Note the provenance of what you retrieved. If the catalog is
unavailable and you are working from cached or assumed structure, that
limitation belongs in the report and should reduce your stated confidence.

### 3. Trace impact

Walk downstream from the defect. Classify every reachable asset as **affected**
or **cleared**, and give a reason for each. Escalate when the path reaches a
deployed model or a production endpoint — that is the difference between a data
problem and a production incident.

### 4. Localise the root cause

Walk *upstream* to find where the defect first appears. The origin is the
earliest asset that exhibits it, not the first place it was noticed. Where more
than one explanation fits, rank hypotheses and state what evidence separates
them. Give a confidence figure and explain what produced it.

### 5. Propose remediation

An ordered plan. For each step: what it does, what it touches, and how to roll
it back. Distinguish containment (stop the harm now) from repair (fix the
cause).

### 6. Classify safety

Mark every step that mutates shared state — catalog writes, table drops,
pipeline reruns, model rollbacks. Those require explicit human approval before
execution. Read-only investigation does not.

### 7. Record

After approval, write findings back to the catalog so the next person to look
at these assets sees the incident. Report exactly what was written. If nothing
was written, report that instead, with the reason.

## Severity

Derive it; do not assert it.

| Severity | Condition |
|----------|-----------|
| critical | Checks failed **and** the blast radius reaches a deployed model or production endpoint. |
| warning | A measurable problem that has not yet reached production assets. |
| healthy | Checks passed. The asset is explicitly cleared and remains in service. |

Concluding "healthy" is a real outcome and should be reported with the same
confidence as the others. A tool that can only find problems cannot be trusted
when it reports one.

## Report format

```
SEVERITY: <critical|warning|healthy>  (derived from: <what drove it>)

EVIDENCE
  <check>: observed <value> against threshold <value> (<failing>/<scanned> rows)

ROOT CAUSE
  <asset>, column(s) <columns>
  Confidence <n>% - <what supports it, and what weakens it>
  Alternative considered: <hypothesis> - ruled out because <reason>

BLAST RADIUS
  Affected (<n>): <assets, and why>
  Cleared  (<n>): <assets, and why they are safe>
  Reaches production: <yes|no>

REMEDIATION
  1. <step> - rollback: <how>
  2. ...
  Requires approval: <which steps mutate shared state>

ACTIONS TAKEN
  <exactly what was executed, or "none - awaiting approval",
   or "none - no catalog connection">
```

## Failure modes to avoid

- Reporting severity that was assumed rather than derived from measurements.
- Marking an asset affected because it is downstream, without checking whether
  it consumes the defective column.
- Giving a confidence figure with no explanation of what produced it.
- Executing a mutating step because it seemed obviously correct, without approval.
- Describing a catalog write as successful when no catalog was reachable.
- Reporting "no issues found" when the checks did not actually run — that is a
  different statement, and it must be worded differently.
