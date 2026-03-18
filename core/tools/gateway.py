from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import re
import shlex
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Optional

from core.tools.actions import deploy_staging, git_commit
from core.tools.filesystem import FileSystemTool
from core.tools.http_client import HttpClient
from core.tools.mock_tools import mock_tool


class ToolGateway:
    def __init__(
        self,
        *,
        use_mock: bool = True,
        http_allowlist: Optional[set[str]] = None,
        timeout_seconds: float = 5.0,
        sandbox_dir: Optional[Path] = None,
        approval_checker: Optional[Callable[[str], bool]] = None,
        idempotency_checker: Optional[Callable[[str], bool]] = None,
        idempotency_recorder: Optional[Callable[[str, str, str, str, str], None]] = None,
        action_environment: str = "dev",
    ) -> None:
        self.use_mock = use_mock
        self.http = HttpClient(allowlist=http_allowlist, timeout_seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.fs = FileSystemTool((sandbox_dir or Path(".")).resolve())
        self.approval_checker = approval_checker
        self.idempotency_checker = idempotency_checker
        self.idempotency_recorder = idempotency_recorder
        self.action_environment = action_environment
        self._idempotency_local: set[str] = set()
        self._workspace_allow_run_prefixes = [
            ("pytest",),
            ("python", "-m", "pytest"),
            ("python3", "-m", "pytest"),
            ("npm", "test"),
            ("pnpm", "test"),
            ("yarn", "test"),
            ("go", "test"),
            ("cargo", "test"),
        ]
        self._test_main_path_pattern = re.compile(r"([\"'])[^\"'\n]*data/workspaces/ws_[^/\"'\n]+/app/main\.py\1")

    def _sha(self, payload: Any) -> str:
        return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()[:16]

    def _is_under_workspace(self, target: Path) -> bool:
        root = self.fs.sandbox_dir.resolve()
        resolved = target.resolve()
        return resolved == root or root in resolved.parents

    def _workspace_resolve(self, rel_path: str) -> Path:
        target = (self.fs.sandbox_dir / rel_path).resolve()
        if not self._is_under_workspace(target):
            raise PermissionError("path outside workspace")
        return target

    def _workspace_list(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", "."))
        glob_pat = str(args.get("glob", "*"))
        max_entries = max(1, min(int(args.get("max_entries", 200)), 1000))
        target = self._workspace_resolve(rel)
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError("workspace_list path not found")
        items = []
        for child in sorted(target.iterdir(), key=lambda p: p.name):
            if not fnmatch(child.name, glob_pat):
                continue
            items.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": (child.stat().st_size if child.is_file() else None),
                }
            )
            if len(items) >= max_entries:
                break
        return {
            "path": str(target),
            "items": items,
            "provenance": {
                "source_type": "workspace_list",
                "input_hash": self._sha({"path": rel, "glob": glob_pat, "max_entries": max_entries}),
                "output_hash": self._sha(items),
            },
        }

    def _workspace_read(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", ""))
        max_bytes = max(1, min(int(args.get("max_bytes", 1024 * 1024)), 5 * 1024 * 1024))
        target = self._workspace_resolve(rel)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError("workspace_read path not found")
        raw = target.read_bytes()
        clipped = raw[:max_bytes]
        content = clipped.decode("utf-8", errors="replace")
        return {
            "path": str(target),
            "content": content,
            "bytes_read": len(clipped),
            "truncated": len(raw) > len(clipped),
            "provenance": {
                "source_type": "workspace_read",
                "input_hash": self._sha({"path": rel, "max_bytes": max_bytes}),
                "output_hash": self._sha(content),
            },
        }

    def _workspace_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("workspace_search query is required")
        root_rel = str(args.get("path", "."))
        glob_pat = str(args.get("glob", "*"))
        max_hits = max(1, min(int(args.get("max_hits", 200)), 2000))
        root = self._workspace_resolve(root_rel)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError("workspace_search path not found")
        hits: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if len(hits) >= max_hits:
                break
            if not path.is_file():
                continue
            if not fnmatch(path.name, glob_pat):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if query.lower() in line.lower():
                    hits.append({"path": str(path), "line": idx, "snippet": line[:240]})
                    if len(hits) >= max_hits:
                        break
        return {
            "query": query,
            "path": str(root),
            "hits": hits,
            "provenance": {
                "source_type": "workspace_search",
                "input_hash": self._sha({"query": query, "path": root_rel, "glob": glob_pat, "max_hits": max_hits}),
                "output_hash": self._sha(hits),
            },
        }

    def _workspace_write(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", ""))
        content = str(args.get("content", ""))
        content, normalization = self._normalize_test_module_paths(path=rel, content=content)
        target = self._workspace_resolve(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        out = {
            "path": str(target),
            "bytes_written": len(content.encode("utf-8")),
            "provenance": {
                "source_type": "workspace_write",
                "input_hash": self._sha({"path": rel, "content_len": len(content)}),
                "output_hash": self._sha({"path": str(target), "bytes": len(content.encode("utf-8"))}),
            },
        }
        if normalization is not None:
            out["normalization"] = normalization
        return out

    def _normalize_test_module_paths(self, *, path: str, content: str) -> tuple[str, dict[str, Any] | None]:
        normalized_path = str(path).replace("\\", "/").lower()
        is_python_test = normalized_path.endswith(".py") and (
            "/tests/" in normalized_path or normalized_path.endswith("tests/test_main.py") or "/test_" in normalized_path
        )
        if not is_python_test or "data/workspaces/ws_" not in content:
            return content, None
        replaced, count = self._test_main_path_pattern.subn(r"\1app/main.py\1", content)
        if count <= 0:
            return content, None
        return replaced, {
            "rule": "normalize_workspace_prefixed_app_main_path",
            "replacements": count,
        }

    def _workspace_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        diff = str(args.get("diff", ""))
        if not diff.strip():
            raise ValueError("workspace_patch diff is required")
        proc = subprocess.run(
            ["patch", "-p0", "--forward", "--reject-file", "-"],
            cwd=str(self.fs.sandbox_dir),
            input=diff,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"workspace_patch failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return {
            "applied": True,
            "stdout": proc.stdout[-2000:],
            "provenance": {
                "source_type": "workspace_patch",
                "input_hash": self._sha({"diff": diff}),
                "output_hash": self._sha(proc.stdout),
            },
        }

    def _workspace_run(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._workspace_run_for_run(args, run_id=None)

    def _workspace_run_for_run(
        self,
        args: dict[str, Any],
        *,
        run_id: str | None,
        workspace_id: str | None = None,
        enforce_pytest_strict: bool = False,
    ) -> dict[str, Any]:
        cmd = str(args.get("cmd", "")).strip()
        cmd_original = cmd
        if not cmd:
            raise ValueError("workspace_run cmd is required")
        if enforce_pytest_strict:
            cmd = "python3 -m pytest tests -q"
        timeout_s = max(1, min(int(args.get("timeout_s", 30)), 300))
        tokens = tuple(shlex.split(cmd))
        allowed = any(tokens[: len(prefix)] == prefix for prefix in self._workspace_allow_run_prefixes)
        if not allowed:
            raise PermissionError("workspace_run command not allowlisted")
        cwd = self.fs.sandbox_dir
        if workspace_id:
            workspace_root_arg = str(args.get("__workspace_root", "")).strip()
            workspace_root = (
                Path(workspace_root_arg).resolve()
                if workspace_root_arg
                else (self.fs.sandbox_dir / "data" / "workspaces").resolve()
            )
            explicit_workspace = (workspace_root / workspace_id).resolve()
            if explicit_workspace.exists() and explicit_workspace.is_dir():
                cwd = explicit_workspace
        elif run_id:
            workspace_root_arg = str(args.get("__workspace_root", "")).strip()
            workspace_root = (
                Path(workspace_root_arg).resolve()
                if workspace_root_arg
                else (self.fs.sandbox_dir / "data" / "workspaces").resolve()
            )
            run_workspace = (workspace_root / f"ws_{run_id[-12:]}").resolve()
            if run_workspace.exists() and run_workspace.is_dir():
                cwd = run_workspace
        proc = subprocess.run(
            list(tokens),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return {
            "cmd": cmd,
            "cmd_original": cmd_original,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "provenance": {
                "source_type": "workspace_run",
                "input_hash": self._sha({"cmd": cmd, "timeout_s": timeout_s}),
                "output_hash": self._sha({"code": proc.returncode, "out": proc.stdout, "err": proc.stderr}),
            },
        }

    def _search_local_kb(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("search_local_kb query is required")
        max_hits = max(1, min(int(args.get("max_hits", 50)), 500))
        root = (self.fs.sandbox_dir / "data" / "kb").resolve()
        hits: list[dict[str, Any]] = []
        if root.exists():
            for path in root.rglob("*"):
                if len(hits) >= max_hits:
                    break
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for idx, line in enumerate(text.splitlines(), start=1):
                    if query.lower() in line.lower():
                        hits.append({"source": str(path), "line_start": idx, "line_end": idx, "snippet": line[:240]})
                        if len(hits) >= max_hits:
                            break
        return {
            "query": query,
            "hits": hits,
            "provenance": {
                "source_type": "search_local_kb",
                "input_hash": self._sha({"query": query, "max_hits": max_hits}),
                "output_hash": self._sha(hits),
            },
        }

    def _is_write_tool(self, tool: str) -> bool:
        return tool in {"fs_write", "git_commit", "deploy_staging"}

    def _is_approved(self, approval_id: str) -> bool:
        if self.approval_checker is None:
            return False
        return bool(self.approval_checker(approval_id))

    def _has_seen_idempotency(self, key: str) -> bool:
        if key in self._idempotency_local:
            return True
        if self.idempotency_checker is not None and self.idempotency_checker(key):
            return True
        return False

    def _record_idempotency(self, key: str, tool: str, run_id: str, rollback_hint: str, status: str) -> None:
        self._idempotency_local.add(key)
        if self.idempotency_recorder is not None:
            self.idempotency_recorder(key, tool, run_id, rollback_hint, status)

    def execute(
        self,
        *,
        run_id: str,
        tool_calls: list[dict[str, Any]],
        budgets: dict[str, int] | None = None,
        allowed_tools: Optional[list[str]] = None,
        forbidden_tools: Optional[list[str]] = None,
        active_persona_id: str = "general",
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        budgets = budgets or {}
        allowed_set = set(allowed_tools) if allowed_tools is not None else {
            "http_get",
            "fs_read",
            "fs_write",
            "read_file",
            "write_file",
            "search_local_kb",
            "git_commit",
            "deploy_staging",
            "workspace_list",
            "workspace_read",
            "workspace_search",
            "workspace_write",
            "workspace_patch",
            "workspace_run",
        }
        forbidden_set = set(forbidden_tools or [])
        max_total = budgets.get("max_total_tool_calls", len(tool_calls))
        max_http = budgets.get("max_http_get", len(tool_calls))
        max_reads = budgets.get("max_reads", len(tool_calls))
        max_writes = budgets.get("max_writes", len(tool_calls))
        max_bytes = budgets.get("max_bytes", 10_000_000)
        total_used = 0
        http_used = 0
        reads_used = 0
        writes_used = 0
        bytes_used = 0
        for call in tool_calls:
            started = datetime.now(timezone.utc).isoformat()
            tool = call["tool"]
            tc_id = call["tool_call_id"]
            if total_used >= max_total:
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False,
                        "blocked_reason": "budget_exceeded_total",
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            if tool == "http_get" and http_used >= max_http:
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False,
                        "blocked_reason": "budget_exceeded_http_get",
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            if tool in {"fs_read", "read_file", "workspace_read", "workspace_search", "workspace_list", "search_local_kb"} and reads_used >= max_reads:
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False,
                        "blocked_reason": "budget_exceeded_reads",
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            if tool in {"fs_write", "write_file", "workspace_write", "workspace_patch"} and writes_used >= max_writes:
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False,
                        "blocked_reason": "budget_exceeded_writes",
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            allowed = tool in allowed_set and tool not in forbidden_set
            if not allowed:
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False,
                        "blocked_reason": "forbidden_tool",
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            args = call.get("args", {})
            predicted_bytes_delta = 0
            if tool in {"fs_write", "write_file"}:
                predicted_bytes_delta = len(str(args.get("content", "")).encode("utf-8"))
            elif tool == "workspace_write":
                predicted_bytes_delta = len(str(args.get("content", "")).encode("utf-8"))
            elif tool == "workspace_patch":
                predicted_bytes_delta = len(str(args.get("diff", "")).encode("utf-8"))
            if predicted_bytes_delta > 0 and (bytes_used + predicted_bytes_delta) > max_bytes:
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False,
                        "blocked_reason": "budget_exceeded_bytes",
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            if self._is_write_tool(tool):
                approval_id = str(args.get("approval_id", "")).strip()
                if not approval_id:
                    results.append(
                        {
                            "tool_call_id": tc_id,
                            "run_id": run_id,
                            "tool": tool,
                            "allowed": False,
                            "blocked_reason": "approval_required",
                            "started_at": started,
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    continue
                if not self._is_approved(approval_id):
                    results.append(
                        {
                            "tool_call_id": tc_id,
                            "run_id": run_id,
                            "tool": tool,
                            "allowed": False,
                            "blocked_reason": "approval_denied",
                            "started_at": started,
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    continue
                idem = str(args.get("idempotency_key", "")).strip()
                if not idem:
                    results.append(
                        {
                            "tool_call_id": tc_id,
                            "run_id": run_id,
                            "tool": tool,
                            "allowed": False,
                            "blocked_reason": "idempotency_key_missing",
                            "started_at": started,
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    continue
                if self._has_seen_idempotency(idem):
                    results.append(
                        {
                            "tool_call_id": tc_id,
                            "run_id": run_id,
                            "tool": tool,
                            "allowed": False,
                            "blocked_reason": "duplicate_idempotency_key",
                            "started_at": started,
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    continue
                rollback_hint = str(args.get("rollback_hint", "")).strip()
                if not rollback_hint:
                    results.append(
                        {
                            "tool_call_id": tc_id,
                            "run_id": run_id,
                            "tool": tool,
                            "allowed": False,
                            "blocked_reason": "rollback_missing",
                            "started_at": started,
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    continue
                if tool == "deploy_staging" and self.action_environment != "staging":
                    results.append(
                        {
                            "tool_call_id": tc_id,
                            "run_id": run_id,
                            "tool": tool,
                            "allowed": False,
                            "blocked_reason": "deploy_requires_staging_env",
                            "started_at": started,
                            "ended_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    continue
            try:
                if tool == "http_get":
                    url = str(args.get("url", ""))
                    self.http.validate_url(url)
                    timeout_ms = int(args.get("timeout_ms", int(self.timeout_seconds * 1000)))
                    if timeout_ms <= 0:
                        raise TimeoutError("http_get timeout")
                    if self.use_mock:
                        result = mock_tool(tool, args)
                        result["url"] = url
                        result["status"] = 200
                    else:
                        result = self.http.get(url)
                elif tool == "fs_write":
                    if self.use_mock:
                        result = mock_tool(tool, args)
                        result["path"] = str(args.get("path", ""))
                    else:
                        result = self.fs.write(str(args["path"]), str(args.get("content", "")))
                    self._record_idempotency(
                        str(args["idempotency_key"]),
                        tool,
                        run_id,
                        str(args["rollback_hint"]),
                        "succeeded",
                    )
                elif tool == "git_commit":
                    result = git_commit(
                        message=str(args.get("message", "codex: automated commit")),
                        paths=args.get("paths") or [],
                    )
                    self._record_idempotency(
                        str(args["idempotency_key"]),
                        tool,
                        run_id,
                        str(args["rollback_hint"]),
                        "succeeded",
                    )
                elif tool == "deploy_staging":
                    result = deploy_staging(
                        service=str(args.get("service", "unknown")),
                        version=str(args.get("version", "latest")),
                    )
                    self._record_idempotency(
                        str(args["idempotency_key"]),
                        tool,
                        run_id,
                        str(args["rollback_hint"]),
                        "succeeded",
                    )
                elif tool == "read_file":
                    result = self.fs.read(str(args.get("path", "")))
                elif tool == "write_file":
                    path = str(args.get("path", ""))
                    content = str(args.get("content", ""))
                    content, normalization = self._normalize_test_module_paths(path=path, content=content)
                    result = self.fs.write(path, content)
                    if normalization is not None:
                        result["normalization"] = normalization
                elif tool == "search_local_kb":
                    result = self._search_local_kb(args)
                elif tool == "workspace_list":
                    result = self._workspace_list(args)
                elif tool == "workspace_read":
                    result = self._workspace_read(args)
                elif tool == "workspace_search":
                    result = self._workspace_search(args)
                elif tool in {"workspace_write", "workspace_patch"}:
                    if active_persona_id != "implementation":
                        results.append(
                            {
                                "tool_call_id": tc_id,
                                "run_id": run_id,
                                "tool": tool,
                                "allowed": False,
                                "blocked_reason": "persona_forbidden_tool",
                                "started_at": started,
                                "ended_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        continue
                    result = self._workspace_write(args) if tool == "workspace_write" else self._workspace_patch(args)
                elif tool == "workspace_run":
                    if active_persona_id != "qa_test":
                        results.append(
                            {
                                "tool_call_id": tc_id,
                                "run_id": run_id,
                                "tool": tool,
                                "allowed": False,
                                "blocked_reason": "workspace_run_requires_qa_test",
                                "started_at": started,
                                "ended_at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        continue
                    result = self._workspace_run_for_run(
                        args,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        enforce_pytest_strict=True,
                    )
                elif self.use_mock:
                    result = mock_tool(tool, args)
                else:
                    result = self.fs.read(str(args["path"]))
                total_used += 1
                if tool == "http_get":
                    http_used += 1
                if tool in {"fs_read", "read_file", "workspace_read", "workspace_search", "workspace_list", "search_local_kb"}:
                    reads_used += 1
                if tool in {"fs_write", "write_file", "workspace_write", "workspace_patch"}:
                    writes_used += 1
                if tool in {"fs_write", "write_file"}:
                    bytes_used += len(str(args.get("content", "")).encode("utf-8"))
                elif tool == "workspace_write":
                    bytes_used += int((result or {}).get("bytes_written", 0))
                elif tool == "workspace_patch":
                    bytes_used += len(str(args.get("diff", "")).encode("utf-8"))
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": True,
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "result": result,
                    }
                )
            except Exception as exc:  # pragma: no cover
                if self._is_write_tool(tool):
                    idem_key = str(args.get("idempotency_key", "")).strip()
                    rollback_hint = str(args.get("rollback_hint", "")).strip()
                    if idem_key:
                        self._record_idempotency(idem_key, tool, run_id, rollback_hint, "failed")
                results.append(
                    {
                        "tool_call_id": tc_id,
                        "run_id": run_id,
                        "tool": tool,
                        "allowed": False if isinstance(exc, (PermissionError, TimeoutError)) else True,
                        "blocked_reason": (
                            (
                                "workspace_run_disallowed_command"
                                if isinstance(exc, PermissionError) and tool == "workspace_run"
                                else ("domain_not_allowlisted" if isinstance(exc, PermissionError) else None)
                            )
                            or (
                                "workspace_run_timeout"
                                if isinstance(exc, subprocess.TimeoutExpired)
                                else ("timeout" if isinstance(exc, TimeoutError) else None)
                            )
                        ),
                        "started_at": started,
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                )
        return results
