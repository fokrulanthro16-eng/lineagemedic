"""Narration providers. Optional, and never load-bearing.

The deterministic engine produces the entire diagnosis on its own. A narrator
only restates evidence that has already been computed, in prose. If no model is
available, or a model errors, or a model returns nonsense, the diagnosis is
unaffected - severity, impact, root cause, and remediation are all decided
before a narrator is consulted.

:class:`DeterministicNarrator` is the default. It is a template over measured
facts, so it cannot hallucinate: every number it prints was passed to it.

:class:`OllamaNarrator` calls a locally running Ollama model. It is given only
the computed evidence and is instructed to add nothing. Its output is validated
by :func:`_mentions_only_known_assets` before use; if the model invents an asset
name, the narration is discarded and the deterministic text is used instead.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from lineagemedic.models import CheckStatus, Diagnosis, Severity

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"

_SYSTEM_PROMPT = """You are a data incident reporter. You will be given the \
results of a completed automated investigation.

Write 2-4 sentences summarising it for an on-call engineer.

Absolute rules:
- Use ONLY facts present in the input. Invent nothing.
- Do not name any dataset, model, or endpoint that is not listed in the input.
- Do not invent numbers, percentages, causes, or remediation steps.
- Do not speculate about anything beyond the evidence given.
Write plainly, with no preamble and no bullet points."""


class Narrator(Protocol):
    """Turns a completed diagnosis into a short prose summary."""

    @property
    def provider_name(self) -> str: ...

    def available(self) -> tuple[bool, str]:
        """Return ``(usable, human_readable_detail)``. Must not raise."""
        ...

    def narrate(self, diagnosis: Diagnosis) -> str: ...


class DeterministicNarrator:
    """Template-based narration over measured facts. Always available."""

    provider_name = "deterministic"

    def available(self) -> tuple[bool, str]:
        return True, "Deterministic narrator: evidence-only, no model required."

    def narrate(self, diagnosis: Diagnosis) -> str:
        if diagnosis.severity is Severity.HEALTHY:
            return (
                f"All {len(diagnosis.quality_checks)} quality checks passed. "
                f"{diagnosis.impact.unaffected_count} assets were examined and none "
                "showed a defect, so no remediation is proposed and nothing was "
                "removed from service."
            )

        failed = [c for c in diagnosis.quality_checks if c.status is CheckStatus.FAIL]
        top = diagnosis.root_causes[0] if diagnosis.root_causes else None
        owner = diagnosis.primary_owner

        sentences = [
            f"{len(failed)} of {len(diagnosis.quality_checks)} quality checks failed on "
            f"{diagnosis.title.lower()}."
        ]
        if top is not None:
            sentences.append(f"{top.summary}, at {top.confidence:.0%} confidence.")
        sentences.append(
            f"{diagnosis.impact.affected_count} assets are downstream of the failure, "
            f"including {len(diagnosis.impact.ml_models_affected)} ML model(s) and "
            f"{len(diagnosis.impact.production_endpoints_affected)} production endpoint(s); "
            f"{diagnosis.impact.unaffected_count} unrelated assets were cleared and stay "
            "in service."
        )
        if owner is not None:
            sentences.append(f"{owner.display_name} owns the suspected asset.")
        if diagnosis.safety.requires_human_approval:
            sentences.append(
                f"{len(diagnosis.remediation)} reversible actions are proposed and are "
                "held at the approval gate."
            )
        return " ".join(sentences)


class OllamaNarrator:
    """Narration via a locally running Ollama model.

    Strictly optional. Requires no API key, no account, and no network egress
    beyond localhost.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._fallback = DeterministicNarrator()

    @property
    def provider_name(self) -> str:
        return f"ollama:{self._model}"

    def available(self) -> tuple[bool, str]:
        """Check that Ollama is up and the configured model is pulled."""
        try:
            response = httpx.get(f"{self._base_url}/api/tags", timeout=3.0)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
        except Exception as exc:
            return False, f"Ollama unreachable at {self._base_url}: {exc.__class__.__name__}"
        if not any(m.startswith(self._model.split(":")[0]) for m in models):
            return False, (
                f"Ollama is running but model {self._model!r} is not pulled. "
                f"Run: ollama pull {self._model}"
            )
        return True, f"Ollama ready at {self._base_url} with model {self._model}."

    def narrate(self, diagnosis: Diagnosis) -> str:
        """Ask the model to restate the evidence, falling back on any problem."""
        deterministic = self._fallback.narrate(diagnosis)
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "system": _SYSTEM_PROMPT,
                    "prompt": _build_prompt(diagnosis),
                    "stream": False,
                    # Low temperature: this is a restatement task, not creative
                    # writing. Determinism is worth more than fluency here.
                    "options": {"temperature": 0.1},
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            text = str(response.json().get("response", "")).strip()
        except Exception as exc:
            logger.warning("Ollama narration failed (%s); using deterministic text", exc)
            return deterministic

        if not text:
            return deterministic
        if not _mentions_only_known_assets(text, diagnosis):
            logger.warning("Ollama narration referenced an unknown asset; discarding")
            return deterministic
        return text


def _build_prompt(diagnosis: Diagnosis) -> str:
    """Render the computed evidence as the model's only source of facts."""
    lines = [
        f"Severity: {diagnosis.severity.value}",
        f"Title: {diagnosis.title}",
        "",
        "Quality check results:",
    ]
    for check in diagnosis.quality_checks:
        lines.append(
            f"- [{check.status.value.upper()}] {check.check_id}: observed "
            f"{check.observed_value} vs threshold {check.threshold}, "
            f"{check.failing_rows}/{check.rows_scanned} rows violating"
        )
    lines += ["", "Assets in the blast radius:"]
    lines += [f"- {a.name} ({a.kind.value}): {a.rationale}" for a in diagnosis.impact.affected]
    lines += ["", "Assets examined and cleared:"]
    lines += [f"- {a.name} ({a.kind.value})" for a in diagnosis.impact.unaffected]
    if diagnosis.root_causes:
        top = diagnosis.root_causes[0]
        lines += [
            "",
            f"Most likely cause: {top.summary} (confidence {top.confidence:.0%})",
            f"Reasoning: {top.reasoning}",
        ]
    if diagnosis.remediation:
        lines += ["", "Proposed actions:"]
        lines += [f"- {a.title}" for a in diagnosis.remediation]
    return "\n".join(lines)


def _mentions_only_known_assets(text: str, diagnosis: Diagnosis) -> bool:
    """Reject narration naming an asset that is not in the lineage graph.

    A cheap, high-value guard: the most damaging hallucination in this domain
    is a confidently named table that does not exist. Asset names in this graph
    are distinctive (``raw_patients``, ``billing_summary``), so scanning for
    snake_case tokens catches invented names without flagging ordinary prose.
    """
    known = {a.name.lower() for a in diagnosis.lineage.assets}
    lowered = text.lower()
    return all(token in known for token in _snake_case_tokens(lowered))


def _snake_case_tokens(text: str) -> list[str]:
    """Extract snake_case identifiers, which in this domain are asset names."""
    import re

    return re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text)


def build_narrator(
    provider: str,
    *,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
) -> Narrator:
    """Construct the configured narrator. Unknown names fall back to deterministic."""
    if provider == "ollama":
        return OllamaNarrator(base_url=ollama_url, model=ollama_model)
    if provider != "deterministic":
        logger.warning("Unknown LLM provider %r; using deterministic narrator", provider)
    return DeterministicNarrator()
