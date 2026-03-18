from __future__ import annotations

from pathlib import Path


class FileSystemTool:
    def __init__(self, sandbox_dir: Path) -> None:
        self.sandbox_dir = sandbox_dir.resolve()

    def read(self, path: str) -> dict:
        target = (self.sandbox_dir / path).resolve()
        if not str(target).startswith(str(self.sandbox_dir)):
            raise PermissionError("path outside sandbox")
        return {"path": str(target), "content": target.read_text(encoding="utf-8")}

    def write(self, path: str, content: str) -> dict:
        target = (self.sandbox_dir / path).resolve()
        if not str(target).startswith(str(self.sandbox_dir)):
            raise PermissionError("path outside sandbox")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}
