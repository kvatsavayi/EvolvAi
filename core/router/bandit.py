from __future__ import annotations

import random


def choose_weighted(weights: dict[str, float], epsilon: float = 0.15) -> str:
    if not weights:
        raise ValueError("weights cannot be empty")
    if random.random() < epsilon:
        return random.choice(list(weights.keys()))
    return max(weights, key=weights.get)
