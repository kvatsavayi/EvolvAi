from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from typing import Any


def _normalize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump())
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, set):
        norm_items = [_normalize(v) for v in value]
        return sorted(norm_items, key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float not allowed in canonical JSON")
        if value == 0.0:
            return 0.0
    return value


def canonical_json_dumps(value: Any) -> str:
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json_dumps(value).encode("utf-8")).hexdigest()


def short_hash_id(prefix: str, value: Any, length: int = 12) -> str:
    return f"{prefix}_{canonical_sha256(value)[:length]}"
