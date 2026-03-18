from __future__ import annotations

import hashlib
import math
from collections import deque
from typing import Any


GRID_SIZE = 10
GRID_CELLS = GRID_SIZE * GRID_SIZE
SPARSE_ONES = 20


def _empty_grid() -> list[list[int]]:
    return [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]


def coerce_dream_grid(value: Any) -> list[list[int]] | None:
    if not isinstance(value, list) or len(value) != GRID_SIZE:
        return None
    coerced: list[list[int]] = []
    for row in value:
        if not isinstance(row, list) or len(row) != GRID_SIZE:
            return None
        out_row: list[int] = []
        for cell in row:
            if isinstance(cell, bool):
                out_row.append(1 if cell else 0)
            elif isinstance(cell, int) and cell in (0, 1):
                out_row.append(cell)
            else:
                return None
        coerced.append(out_row)
    return coerced


def _behavior_seed(output: dict[str, Any]) -> str:
    response = output.get("response") or {}
    plan = output.get("plan") or []
    tool_calls = output.get("tool_calls") or []
    payload = {
        "response_type": str(response.get("type", "")),
        "content": str(response.get("content", "")),
        "plan_intents": [str(step.get("intent", "")) for step in plan if isinstance(step, dict)],
        "tool_names": [str(call.get("tool", "")) for call in tool_calls if isinstance(call, dict)],
    }
    return str(payload)


def make_sparse_dream_grid(seed: str, ones: int = SPARSE_ONES) -> list[list[int]]:
    ones = max(1, min(GRID_CELLS - 1, int(ones)))
    grid = _empty_grid()
    chosen: set[int] = set()
    cursor = 0
    while len(chosen) < ones:
        digest = hashlib.sha256(f"{seed}:{cursor}".encode("utf-8")).digest()
        for b in digest:
            idx = b % GRID_CELLS
            chosen.add(idx)
            if len(chosen) >= ones:
                break
        cursor += 1
    for idx in chosen:
        r, c = divmod(idx, GRID_SIZE)
        grid[r][c] = 1
    return grid


def ensure_dream_grid(output: dict[str, Any]) -> list[list[int]]:
    existing = coerce_dream_grid(output.get("dream_grid_bool"))
    if existing is not None:
        output["dream_grid_bool"] = existing
        return existing
    grid = make_sparse_dream_grid(_behavior_seed(output))
    output["dream_grid_bool"] = grid
    return grid


def _largest_component_size(grid: list[list[int]]) -> int:
    seen: set[tuple[int, int]] = set()
    best = 0
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE):
            if grid[r][c] != 1 or (r, c) in seen:
                continue
            q: deque[tuple[int, int]] = deque([(r, c)])
            seen.add((r, c))
            size = 0
            while q:
                cr, cc = q.popleft()
                size += 1
                for nr, nc in ((cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)):
                    if nr < 0 or nr >= GRID_SIZE or nc < 0 or nc >= GRID_SIZE:
                        continue
                    if grid[nr][nc] != 1 or (nr, nc) in seen:
                        continue
                    seen.add((nr, nc))
                    q.append((nr, nc))
            if size > best:
                best = size
    return best


def _symmetry_score(grid: list[list[int]]) -> float:
    total_pairs = 0
    matches = 0
    for r in range(GRID_SIZE):
        for c in range(GRID_SIZE // 2):
            total_pairs += 1
            if grid[r][c] == grid[r][GRID_SIZE - 1 - c]:
                matches += 1
    for r in range(GRID_SIZE // 2):
        for c in range(GRID_SIZE):
            total_pairs += 1
            if grid[r][c] == grid[GRID_SIZE - 1 - r][c]:
                matches += 1
    if total_pairs == 0:
        return 0.0
    return round(matches / total_pairs, 4)


def analyze_dream_grid(grid: list[list[int]]) -> dict[str, Any]:
    popcount = sum(cell for row in grid for cell in row)
    density = popcount / GRID_CELLS
    entropy = 0.0
    if 0.0 < density < 1.0:
        entropy = -(density * math.log2(density) + (1.0 - density) * math.log2(1.0 - density))
    canonical = "".join(str(cell) for row in grid for cell in row)
    fp = f"fp_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
    return {
        "grid_fp": fp,
        "popcount": int(popcount),
        "density": round(density, 4),
        "entropy": round(entropy, 4),
        "largest_component_size": int(_largest_component_size(grid)),
        "symmetry_score": _symmetry_score(grid),
    }
