from __future__ import annotations


def shell_exec(*_: object, **__: object) -> dict:
    raise PermissionError("shell_exec is disabled in v1")
