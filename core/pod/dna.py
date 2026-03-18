from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from core.observability.canonical import canonical_sha256, short_hash_id


@dataclass
class DNA:
    dna_id: str
    version: int
    created_at: str
    persona: Literal["planner", "dev", "tester", "reviewer", "release", "maintainer", "general"]
    system_constraints: List[str]
    style: Dict[str, str]
    prompt_template: Dict[str, str]
    tool_policy_overrides: Optional[Dict[str, Any]] = None
    lineage: Dict[str, Any] = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_dna_id(prompt_template: Dict[str, str], constraints: List[str], style: Optional[Dict[str, str]] = None) -> str:
    stable_payload = {
        "prompt_template": prompt_template,
        "system_constraints": constraints,
        "style": style or {},
    }
    return short_hash_id("dna", stable_payload, length=12)


def make_dna_hash(dna_payload: Dict[str, Any]) -> str:
    return canonical_sha256(dna_payload)


def seed_dna(persona: str = "general", version: int = 1) -> DNA:
    prompt_template = {
        "system": "You are a reliable execution agent.",
        "instructions": (
            "Answer directly and concisely. "
            "When workflow directives specify required tools, emit valid tool_calls first and keep response concise. "
            "For QA personas, prioritize behavioral endpoint checks over source-text assertions."
        ),
        "output_contract": "Follow the provided output contract exactly, including tool_calls when actions are required.",
    }
    constraints = [
        "If the request is answerable directly, answer directly. Do not describe steps.",
        "Avoid meta-optimization narratives.",
        "Follow output schema exactly.",
        "Use tools only via provided tool calls.",
        "Do not claim tool usage when no tool call was made.",
        "If required tools are specified in context, include them in tool_calls.",
        "Prefer executable behavioral verification over static code-string checks.",
    ]
    return DNA(
        dna_id=make_dna_id(prompt_template, constraints, {"verbosity": "low", "format": "structured", "risk_posture": "balanced"}),
        version=version,
        created_at=utc_now_iso(),
        persona=persona,  # type: ignore[arg-type]
        system_constraints=constraints,
        style={"verbosity": "low", "format": "structured", "risk_posture": "balanced"},
        prompt_template=prompt_template,
        tool_policy_overrides=None,
        lineage={"parents": [], "mutation_id": None},
    )
