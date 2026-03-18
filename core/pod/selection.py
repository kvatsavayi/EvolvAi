from __future__ import annotations

from typing import Any

from core.pod.dna import DNA


def select_best_dna(candidates: list[DNA], scores: dict[str, float]) -> DNA:
    if not candidates:
        raise ValueError("no DNA candidates")
    return max(candidates, key=lambda dna: scores.get(dna.dna_id, 0.0))


def select_by_pass_rate(candidates: list[DNA], pass_rates: dict[str, float]) -> DNA:
    if not candidates:
        raise ValueError("no DNA candidates")
    # Primary: pass-rate, tie-break: newer DNA version.
    return max(candidates, key=lambda dna: (pass_rates.get(dna.dna_id, 0.0), dna.version))


def update_population(run: dict[str, Any], judge_result: dict[str, Any], external_signals: dict[str, float]) -> None:
    # v1 selector hook: intentionally no return of scores/state to executor path.
    _ = (run, judge_result, external_signals)
