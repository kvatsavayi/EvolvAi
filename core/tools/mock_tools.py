from __future__ import annotations


def mock_tool(name: str, args: dict) -> dict:
    return {"tool": name, "args": args, "status": "ok", "mock": True}
