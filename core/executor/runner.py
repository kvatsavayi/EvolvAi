from __future__ import annotations

from datetime import datetime, timezone
import ast
import json
import os
import re
from typing import Any, Optional, Protocol
from urllib import request as urllib_request
from urllib.error import URLError

from core.executor.prompts import (
    build_executor_prompt,
    executor_prompt_hash,
    executor_prompt_template_id,
    render_executor_prompt,
    render_prompt_payload,
)
from core.executor.sandbox import (
    assert_no_forbidden_executor_payload,
    detect_survival_awareness_violation,
    assert_no_survival_awareness,
    assert_tool_intents,
)
from core.executor.sanitize import normalize_response_content
from core.observability.dream_grid import ensure_dream_grid
from core.pod.dna import DNA
from core.snapshot.schema import Snapshot


def _persona_key(snapshot: Snapshot | None) -> str:
    if snapshot is None:
        return ""
    state = snapshot.context.state or {}
    persona = str(state.get("persona_id") or "").strip().upper()
    return re.sub(r"[^A-Z0-9_]", "_", persona)


def _persona_env(snapshot: Snapshot | None, key: str) -> str | None:
    suffix = _persona_key(snapshot)
    if suffix:
        scoped = os.getenv(f"{key}_{suffix}")
        if scoped is not None and scoped.strip():
            return scoped
    val = os.getenv(key)
    if val is not None and val.strip():
        return val
    return None


class ExecutorBackend(Protocol):
    def generate(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> dict[str, Any]:
        ...


class MockBackend:
    def rail(self) -> dict[str, Any]:
        return {
            "provider": "mock",
            "model": "mock",
            "model_digest": "",
            "base_url": "",
            "seed": None,
        }

    def generate(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> dict[str, Any]:
        user_input = snapshot.request.user_input.lower()
        state = snapshot.context.state or {}
        persona_id = str(state.get("persona_id") or dna.persona)
        workflow_goal = str(state.get("workflow_goal") or "")
        workspace_id = str(state.get("workspace_id") or "").strip()
        tool_calls: list[dict[str, Any]] = []
        if workflow_goal == "hello_fastapi_service":
            if persona_id == "research":
                tool_calls.append(
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "search_local_kb",
                        "args": {"query": "fastapi hello service", "max_hits": 10},
                        "reason": "collect local references",
                    }
                )
                content = "Collected local FastAPI references and constraints."
            elif persona_id == "implementation":
                ws = workspace_id or f"ws_{run_id[-8:]}"
                base = f"data/workspaces/{ws}"
                tool_calls.extend(
                    [
                        {
                            "tool_call_id": f"tc_{run_id[-6:]}_1",
                            "tool": "write_file",
                            "args": {
                                "path": f"{base}/app/main.py",
                                "content": (
                                    "from fastapi import FastAPI\\n\\n"
                                    "app = FastAPI()\\n\\n"
                                    "@app.get('/hello')\\n"
                                    "def hello() -> dict[str, str]:\\n"
                                    "    return {'message': 'hello'}\\n"
                                ),
                            },
                            "reason": "create service",
                        },
                        {
                            "tool_call_id": f"tc_{run_id[-6:]}_2",
                            "tool": "write_file",
                            "args": {
                                "path": f"{base}/tests/test_main.py",
                                "content": (
                                    "from pathlib import Path\\n"
                                    "import importlib.util\\n"
                                    "from fastapi.testclient import TestClient\\n\\n"
                                    "def _load_app_module():\\n"
                                    f"    p = Path('{base}/app/main.py')\\n"
                                    "    spec = importlib.util.spec_from_file_location('workspace_hello_app', p)\\n"
                                    "    module = importlib.util.module_from_spec(spec)\\n"
                                    "    assert spec is not None and spec.loader is not None\\n"
                                    "    spec.loader.exec_module(module)\\n"
                                    "    return module\\n\\n"
                                    "def test_hello_endpoint_behavior() -> None:\\n"
                                    "    mod = _load_app_module()\\n"
                                    "    client = TestClient(mod.app)\\n"
                                    "    resp = client.get('/hello')\\n"
                                    "    assert resp.status_code == 200\\n"
                                    "    assert resp.json() == {'message': 'hello'}\\n"
                                ),
                            },
                            "reason": "create regression test",
                        },
                        {
                            "tool_call_id": f"tc_{run_id[-6:]}_3",
                            "tool": "write_file",
                            "args": {
                                "path": f"{base}/README.md",
                                "content": (
                                    "# Hello FastAPI Service\\n\\n"
                                    "Run:\\n"
                                    "1. pip install fastapi uvicorn\\n"
                                    "2. uvicorn app.main:app --reload\\n"
                                    "3. curl http://127.0.0.1:8000/hello\\n"
                                ),
                            },
                            "reason": "write run instructions",
                        },
                    ]
                )
                content = "Implemented FastAPI hello service and tests in workspace."
            elif persona_id == "qa_test":
                ws = workspace_id or f"ws_{run_id[-8:]}"
                base = f"data/workspaces/{ws}"
                tool_calls.append(
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "workspace_run",
                        "args": {"cmd": f"python3 -m pytest {base}/tests -q", "timeout_s": 60},
                        "reason": "run workspace tests",
                    }
                )
                content = "Executed workspace tests and captured results."
            elif persona_id == "release_ops":
                content = (
                    "Release steps prepared: install deps, start uvicorn, and curl /hello to validate endpoint."
                )
            else:
                content = f"Handled workflow goal {workflow_goal}."
        else:
            content = ""
        if "http" in user_input and "http_get" in snapshot.policies.allowed_tools:
            tool_calls.append(
                {
                    "tool_call_id": f"tc_{run_id[-6:]}_1",
                    "tool": "http_get",
                    "args": {"url": "https://example.com"},
                    "reason": "retrieve referenced resource",
                }
            )
        now = datetime.now(timezone.utc).isoformat()
        if not content:
            if user_input.startswith("ask me") or "ask me " in user_input:
                content = "What topic do you think experts are most likely to revise their view on in the next five years?"
            else:
                content = f"Handled request: {snapshot.request.user_input}"
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {
                "type": "final",
                "content": content,
            },
            "plan": [{"step": 1, "intent": "Answer request"}],
            "tool_calls": tool_calls,
            "trace": {
                "summary": f"Executed at {now} with persona={dna.persona}",
                "signals": {"uncertainty": "low", "assumptions": []},
            },
            "runtime": {
                "backend": "mock",
                "provider": "mock",
                "model": "mock",
                "model_digest": "",
                "model_base_url": "",
                "model_fingerprint": "mock-v1",
                "inference_params": {
                    "temperature": 0.2,
                    "top_p": 1.0,
                    "top_k": 0,
                    "repeat_penalty": 1.0,
                    "max_tokens": 256,
                    "stop": [],
                    "context_window": 4096,
                    "seed": None,
                },
                "token_counts": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "context_truncated": False,
                "truncation_reason": None,
            },
        }


class OllamaModelBackend:
    def rail(self, snapshot: Snapshot | None = None) -> dict[str, Any]:
        provider = "ollama"
        model_name = _persona_env(snapshot, "MODEL_NAME") or _persona_env(snapshot, "LOCAL_MODEL_NAME") or _persona_env(snapshot, "OLLAMA_MODEL") or "llama3.1:8b"
        model_digest = _persona_env(snapshot, "MODEL_DIGEST") or _persona_env(snapshot, "LOCAL_MODEL_FINGERPRINT") or ""
        base_url = _persona_env(snapshot, "MODEL_BASE_URL") or _persona_env(snapshot, "OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        seed_raw = _persona_env(snapshot, "MODEL_SEED") or _persona_env(snapshot, "OLLAMA_SEED") or ""
        seed = int(seed_raw) if seed_raw.strip() else None
        return {
            "provider": provider,
            "model": model_name,
            "model_digest": model_digest,
            "base_url": base_url,
            "seed": seed,
        }

    def generate(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> dict[str, Any]:
        rail = self.rail(snapshot)
        model_name = str(rail["model"])
        model_fingerprint = str(rail["model_digest"])
        base_url = str(rail["base_url"])
        seed = rail.get("seed")
        timeout_seconds = float(_persona_env(snapshot, "MODEL_TIMEOUT_SECONDS") or _persona_env(snapshot, "OLLAMA_TIMEOUT_SECONDS") or "60")
        endpoint = f"{base_url.rstrip('/')}/api/generate"
        prompt = render_executor_prompt(dna, snapshot)
        temperature = float(_persona_env(snapshot, "MODEL_TEMPERATURE") or _persona_env(snapshot, "OLLAMA_TEMPERATURE") or "0.2")
        top_p = float(_persona_env(snapshot, "MODEL_TOP_P") or _persona_env(snapshot, "OLLAMA_TOP_P") or "1.0")
        top_k = int(_persona_env(snapshot, "MODEL_TOP_K") or _persona_env(snapshot, "OLLAMA_TOP_K") or "40")
        repeat_penalty = float(_persona_env(snapshot, "MODEL_REPEAT_PENALTY") or _persona_env(snapshot, "OLLAMA_REPEAT_PENALTY") or "1.0")
        max_tokens = int(_persona_env(snapshot, "MODEL_MAX_TOKENS") or _persona_env(snapshot, "OLLAMA_MAX_TOKENS") or "256")
        workflow_goal = str(((snapshot.context.state or {}).get("workflow_goal") if snapshot.context else "") or "")
        if workflow_goal and max_tokens < 320:
            max_tokens = 320
        context_window = int(
            _persona_env(snapshot, "MODEL_CONTEXT_WINDOW")
            or _persona_env(snapshot, "OLLAMA_CONTEXT_WINDOW")
            or _persona_env(snapshot, "OLLAMA_NUM_CTX")
            or "4096"
        )
        stop_raw = _persona_env(snapshot, "MODEL_STOP_SEQUENCES") or _persona_env(snapshot, "OLLAMA_STOP_SEQUENCES") or ""
        stop_sequences = [s for s in [x.strip() for x in stop_raw.split(",")] if s]
        req_payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "repeat_penalty": repeat_penalty,
                "num_predict": max_tokens,
                "num_ctx": context_window,
            },
        }
        if seed is not None:
            req_payload["options"]["seed"] = int(seed)
        if stop_sequences:
            req_payload["options"]["stop"] = stop_sequences
        body = json.dumps(req_payload).encode("utf-8")
        generated = ""
        uncertainty = "low"
        assumptions: list[str] = []
        runtime_error: dict[str, Any] | None = None
        try:
            req = urllib_request.Request(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            generated = str(payload.get("response", "")).strip()
            if not generated:
                generated = f"[{model_name}] Empty response from ollama."
                uncertainty = "high"
                assumptions.append("ollama returned empty response")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            generated = f"[{model_name}] Local model unavailable: {type(exc).__name__}: {exc}"
            uncertainty = "high"
            assumptions.append("ollama connection failure")
            runtime_error = {
                "error_type": "MODEL_UNAVAILABLE",
                "stage": "inference",
                "reason_code": "provider_error",
                "provider": "ollama",
                "message": f"{type(exc).__name__}: {exc}",
            }

        now = datetime.now(timezone.utc).isoformat()
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(generated) // 4)
        runtime_payload: dict[str, Any] = {
            "backend": "local",
            "provider": "ollama",
            "model": model_name,
            "model_digest": model_fingerprint,
            "model_base_url": base_url,
            "model_fingerprint": model_fingerprint,
            "inference_params": {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "repeat_penalty": repeat_penalty,
                "max_tokens": max_tokens,
                "stop": stop_sequences,
                "context_window": context_window,
                "seed": seed,
            },
            "token_counts": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "context_truncated": False,
            "truncation_reason": None,
        }
        if runtime_error is not None:
            runtime_payload["error"] = runtime_error
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": generated},
            "plan": [{"step": 1, "intent": "Answer request"}],
            "tool_calls": [],
            "trace": {
                "summary": f"Local model backend={model_name} at {now}",
                "signals": {"uncertainty": uncertainty, "assumptions": assumptions},
            },
            "runtime": runtime_payload,
        }


class LlamaCppModelBackend:
    def rail(self, snapshot: Snapshot | None = None) -> dict[str, Any]:
        model_name = _persona_env(snapshot, "MODEL_NAME") or "llama3.1:8b"
        model_digest = _persona_env(snapshot, "MODEL_DIGEST") or ""
        base_url = _persona_env(snapshot, "MODEL_BASE_URL") or "http://127.0.0.1:8080"
        seed_raw = _persona_env(snapshot, "MODEL_SEED") or ""
        seed = int(seed_raw) if seed_raw.strip() else None
        return {
            "provider": "llamacpp",
            "model": model_name,
            "model_digest": model_digest,
            "base_url": base_url,
            "seed": seed,
        }

    def generate(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> dict[str, Any]:
        rail = self.rail(snapshot)
        model_name = str(rail["model"])
        model_digest = str(rail["model_digest"])
        base_url = str(rail["base_url"])
        seed = rail.get("seed")
        timeout_seconds = float(_persona_env(snapshot, "MODEL_TIMEOUT_SECONDS") or "60")
        endpoint = f"{base_url.rstrip('/')}/completion"
        prompt = render_executor_prompt(dna, snapshot)
        temperature = float(_persona_env(snapshot, "MODEL_TEMPERATURE") or "0.2")
        top_p = float(_persona_env(snapshot, "MODEL_TOP_P") or "1.0")
        top_k = int(_persona_env(snapshot, "MODEL_TOP_K") or "40")
        repeat_penalty = float(_persona_env(snapshot, "MODEL_REPEAT_PENALTY") or "1.0")
        max_tokens = int(_persona_env(snapshot, "MODEL_MAX_TOKENS") or "256")
        context_window = int(_persona_env(snapshot, "MODEL_CONTEXT_WINDOW") or "4096")
        stop_raw = _persona_env(snapshot, "MODEL_STOP_SEQUENCES") or ""
        stop_sequences = [s for s in [x.strip() for x in stop_raw.split(",")] if s]
        req_payload: dict[str, Any] = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "repeat_penalty": repeat_penalty,
            "n_ctx": context_window,
            "stop": stop_sequences,
        }
        if seed is not None:
            req_payload["seed"] = int(seed)
        body = json.dumps(req_payload).encode("utf-8")
        generated = ""
        uncertainty = "low"
        assumptions: list[str] = []
        runtime_error: dict[str, Any] | None = None
        try:
            req = urllib_request.Request(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            generated = str(payload.get("content", "")).strip()
            if not generated:
                generated = f"[{model_name}] Empty response from llama.cpp."
                uncertainty = "high"
                assumptions.append("llamacpp returned empty response")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            generated = f"[{model_name}] llama.cpp unavailable: {type(exc).__name__}: {exc}"
            uncertainty = "high"
            assumptions.append("llamacpp connection failure")
            runtime_error = {
                "error_type": "MODEL_UNAVAILABLE",
                "stage": "inference",
                "reason_code": "provider_error",
                "provider": "llamacpp",
                "message": f"{type(exc).__name__}: {exc}",
            }
        now = datetime.now(timezone.utc).isoformat()
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(generated) // 4)
        runtime_payload: dict[str, Any] = {
            "backend": "llamacpp",
            "provider": "llamacpp",
            "model": model_name,
            "model_digest": model_digest,
            "model_base_url": base_url,
            "model_fingerprint": model_digest,
            "inference_params": {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "repeat_penalty": repeat_penalty,
                "max_tokens": max_tokens,
                "stop": stop_sequences,
                "context_window": context_window,
                "seed": seed,
            },
            "token_counts": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "context_truncated": False,
            "truncation_reason": None,
        }
        if runtime_error is not None:
            runtime_payload["error"] = runtime_error
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": generated},
            "plan": [{"step": 1, "intent": "Answer request"}],
            "tool_calls": [],
            "trace": {
                "summary": f"Llama.cpp backend={model_name} at {now}",
                "signals": {"uncertainty": uncertainty, "assumptions": assumptions},
            },
            "runtime": runtime_payload,
        }


class RemoteModelBackend:
    def rail(self, snapshot: Snapshot | None = None) -> dict[str, Any]:
        provider = str(_persona_env(snapshot, "MODEL_PROVIDER") or "remote").strip().lower()
        rail_provider = "openai" if provider in {"openai", "chatgpt"} else "remote"
        model_name = _persona_env(snapshot, "MODEL_NAME") or "gpt-4o-mini"
        model_digest = _persona_env(snapshot, "MODEL_DIGEST") or ""
        base_url = _persona_env(snapshot, "MODEL_BASE_URL") or "https://api.openai.com/v1"
        seed_raw = _persona_env(snapshot, "MODEL_SEED") or ""
        seed = int(seed_raw) if seed_raw.strip() else None
        return {
            "provider": rail_provider,
            "model": model_name,
            "model_digest": model_digest,
            "base_url": base_url,
            "seed": seed,
        }

    def generate(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> dict[str, Any]:
        rail = self.rail(snapshot)
        model_name = str(rail["model"])
        model_digest = str(rail["model_digest"])
        base_url = str(rail["base_url"]).rstrip("/")
        seed = rail.get("seed")
        timeout_seconds = float(_persona_env(snapshot, "MODEL_TIMEOUT_SECONDS") or "90")
        api_key = _persona_env(snapshot, "REMOTE_API_KEY") or _persona_env(snapshot, "OPENAI_API_KEY") or ""
        endpoint = f"{base_url}/chat/completions"
        prompt = render_executor_prompt(dna, snapshot)
        temperature = float(_persona_env(snapshot, "MODEL_TEMPERATURE") or "0.2")
        top_p = float(_persona_env(snapshot, "MODEL_TOP_P") or "1.0")
        max_tokens = int(_persona_env(snapshot, "MODEL_MAX_TOKENS") or "256")
        req_payload: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        if seed is not None:
            req_payload["seed"] = int(seed)
        body = json.dumps(req_payload).encode("utf-8")
        generated = ""
        uncertainty = "low"
        assumptions: list[str] = []
        runtime_error: dict[str, Any] | None = None
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
            with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            choices = payload.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            generated = str(message.get("content", "")).strip()
            if not generated:
                generated = f"[{model_name}] Empty response from remote model."
                uncertainty = "high"
                assumptions.append("remote returned empty response")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            generated = f"[{model_name}] Remote model unavailable: {type(exc).__name__}: {exc}"
            uncertainty = "high"
            assumptions.append("remote connection failure")
            runtime_error = {
                "error_type": "MODEL_UNAVAILABLE",
                "stage": "inference",
                "reason_code": "provider_error",
                "provider": "remote",
                "message": f"{type(exc).__name__}: {exc}",
            }
        now = datetime.now(timezone.utc).isoformat()
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(generated) // 4)
        runtime_payload: dict[str, Any] = {
            "backend": "remote",
            "provider": "remote",
            "model": model_name,
            "model_digest": model_digest,
            "model_base_url": base_url,
            "model_fingerprint": model_digest,
            "inference_params": {
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "seed": seed,
            },
            "token_counts": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "context_truncated": False,
            "truncation_reason": None,
        }
        if runtime_error is not None:
            runtime_payload["error"] = runtime_error
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": generated},
            "plan": [{"step": 1, "intent": "Answer request"}],
            "tool_calls": [],
            "trace": {
                "summary": f"Remote backend={model_name} at {now}",
                "signals": {"uncertainty": uncertainty, "assumptions": assumptions},
            },
            "runtime": runtime_payload,
        }


class ExecutorRunner:
    def __init__(self, backend: Optional[ExecutorBackend] = None) -> None:
        if backend is not None:
            self.backend = backend
            self._custom_backend = True
            return
        self._custom_backend = False
        self.backend = self._build_backend(self._provider_for_snapshot(None))

    def _provider_for_snapshot(self, snapshot: Snapshot | None) -> str:
        provider = str(_persona_env(snapshot, "MODEL_PROVIDER") or "").strip().lower()
        if not provider:
            legacy = str(_persona_env(snapshot, "EXECUTOR_BACKEND") or "mock").strip().lower()
            provider = "ollama" if legacy in {"local", "ollama"} else legacy
        return provider

    def _build_backend(self, provider: str) -> ExecutorBackend:
        normalized = provider.strip().lower()
        if normalized == "ollama":
            return OllamaModelBackend()
        if normalized == "llamacpp":
            return LlamaCppModelBackend()
        if normalized in {"remote", "openai", "chatgpt"}:
            return RemoteModelBackend()
        return MockBackend()

    def _backend_for_snapshot(self, snapshot: Snapshot) -> ExecutorBackend:
        if self._custom_backend:
            return self.backend
        return self._build_backend(self._provider_for_snapshot(snapshot))

    def model_rail(self, snapshot: Snapshot | None = None) -> dict[str, Any]:
        backend = self.backend if self._custom_backend else self._build_backend(self._provider_for_snapshot(snapshot))
        if hasattr(backend, "rail"):
            try:
                rail_fn = getattr(backend, "rail")
                rail = dict(rail_fn(snapshot) if snapshot is not None else rail_fn())
                rail.setdefault("provider", "mock")
                rail.setdefault("model", "mock")
                rail.setdefault("model_digest", "")
                rail.setdefault("base_url", "")
                rail.setdefault("seed", None)
                return rail
            except Exception:
                pass
        return {
            "provider": "mock",
            "model": "mock",
            "model_digest": "",
            "base_url": "",
            "seed": None,
        }

    def _extract_json_payload(self, text: str) -> dict[str, Any] | None:
        def _parse_candidate(candidate: str) -> dict[str, Any] | None:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    # Accept Python-dict style payloads often returned by LLMs.
                    parsed = ast.literal_eval(candidate)
                except (ValueError, SyntaxError):
                    return None
            if isinstance(parsed, dict):
                return parsed
            return None

        def _balanced_object_candidates(s: str) -> list[str]:
            out: list[str] = []
            start = s.find("{")
            if start == -1:
                return out
            depth = 0
            in_str = False
            escape = False
            obj_start = -1
            for i, ch in enumerate(s):
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == "{":
                    if depth == 0:
                        obj_start = i
                    depth += 1
                elif ch == "}":
                    if depth > 0:
                        depth -= 1
                        if depth == 0 and obj_start != -1:
                            out.append(s[obj_start : i + 1].strip())
                            obj_start = -1
            return out

        stripped = text.strip()
        candidates: list[str] = []
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)
        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
        if fence_match:
            candidates.append(fence_match.group(1).strip())
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last > first:
            candidates.append(text[first : last + 1].strip())
        candidates.extend(_balanced_object_candidates(text))
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            parsed = _parse_candidate(candidate)
            if parsed is not None:
                return parsed
        return None

    def _maybe_parse_model_contract(self, output: dict[str, Any]) -> None:
        response = output.get("response")
        if not isinstance(response, dict):
            return
        raw_content = response.get("content")
        if not isinstance(raw_content, str):
            return
        payload = self._extract_json_payload(raw_content)
        if not payload:
            return
        parsed_response = payload.get("response", payload if "content" in payload else None)
        if isinstance(parsed_response, dict) and isinstance(parsed_response.get("content"), str):
            response["type"] = str(parsed_response.get("type") or response.get("type") or "final")
            response["content_raw"] = raw_content
            response["content"] = str(parsed_response.get("content") or "")
            structured = parsed_response.get("structured")
            if isinstance(structured, dict):
                response["structured"] = structured
        parsed_calls = payload.get("tool_calls")
        if not isinstance(parsed_calls, list) or output.get("tool_calls"):
            return
        normalized_calls: list[dict[str, Any]] = []
        for idx, call in enumerate(parsed_calls):
            if not isinstance(call, dict):
                continue
            tool = str(call.get("tool") or "").strip()
            args = call.get("args")
            if not tool or not isinstance(args, dict):
                continue
            tool_call_id = str(call.get("tool_call_id") or f"tc_model_{idx + 1}")
            normalized: dict[str, Any] = {"tool_call_id": tool_call_id, "tool": tool, "args": args}
            reason = call.get("reason")
            if isinstance(reason, str) and reason.strip():
                normalized["reason"] = reason.strip()
            normalized_calls.append(normalized)
        if normalized_calls:
            output["tool_calls"] = normalized_calls
            trace = output.get("trace")
            if isinstance(trace, dict):
                signals = trace.get("signals")
                if not isinstance(signals, dict):
                    signals = {}
                    trace["signals"] = signals
                signals["parsed_model_tool_calls"] = True

    def _workflow_tool_calls(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> list[dict[str, Any]]:
        state = snapshot.context.state or {}
        workflow_goal = str(state.get("workflow_goal") or "")
        if workflow_goal not in {"hello_fastapi_service", "weather_station_app", "generic_build_app", "service_bootstrap_app", "ui_page_update"}:
            return []
        persona_id = str(state.get("persona_id") or dna.persona)
        workspace_id = str(state.get("workspace_id") or f"ws_{run_id[-8:]}").strip()
        base = f"data/workspaces/{workspace_id}"
        if workflow_goal == "ui_page_update":
            if persona_id == "research":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "search_local_kb",
                        "args": {"query": "react layout widget patterns", "max_hits": 8},
                        "reason": "collect UI update references",
                    }
                ]
            if persona_id == "implementation":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/apps/ui/src/main.jsx",
                            "content": (
                                "import React from 'react';\n"
                                "import { createRoot } from 'react-dom/client';\n\n"
                                "function App() {\n"
                                "  return (\n"
                                "    <main style={{ padding: '1rem', fontFamily: 'sans-serif' }}>\n"
                                "      <h1>Hello World</h1>\n"
                                "      <p>Header updated by workflow.</p>\n"
                                "    </main>\n"
                                "  );\n"
                                "}\n\n"
                                "createRoot(document.getElementById('root')).render(<App />);\n"
                            ),
                        },
                        "reason": "apply requested UI page update",
                    }
                ]
            if persona_id == "qa_test":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "workspace_run",
                        "args": {"cmd": "npm test", "timeout_s": 60},
                        "reason": "run frontend checks",
                    }
                ]
            return []
        if workflow_goal in {"generic_build_app", "service_bootstrap_app"}:
            if persona_id == "research":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "search_local_kb",
                        "args": {"query": "fastapi CRUD api patterns", "max_hits": 10},
                        "reason": "collect CRUD references",
                    }
                ]
            if persona_id == "implementation":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/app/main.py",
                            "content": (
                                "from fastapi import FastAPI, HTTPException\n"
                                "from pydantic import BaseModel\n\n"
                                "app = FastAPI(title='CRUD App')\n\n"
                                "@app.get('/health')\n"
                                "def health() -> dict[str, str]:\n"
                                "    return {'status': 'ok'}\n\n"
                                "class ItemIn(BaseModel):\n"
                                "    name: str\n\n"
                                "items: dict[int, dict[str, str]] = {}\n"
                                "next_id = 1\n\n"
                                "@app.post('/items')\n"
                                "def create_item(payload: ItemIn) -> dict[str, object]:\n"
                                "    global next_id\n"
                                "    item_id = next_id\n"
                                "    next_id += 1\n"
                                "    items[item_id] = {'id': item_id, 'name': payload.name}\n"
                                "    return items[item_id]\n\n"
                                "@app.get('/items/{item_id}')\n"
                                "def get_item(item_id: int) -> dict[str, object]:\n"
                                "    item = items.get(item_id)\n"
                                "    if item is None:\n"
                                "        raise HTTPException(status_code=404, detail='not found')\n"
                                "    return item\n\n"
                                "@app.put('/items/{item_id}')\n"
                                "def update_item(item_id: int, payload: ItemIn) -> dict[str, object]:\n"
                                "    if item_id not in items:\n"
                                "        raise HTTPException(status_code=404, detail='not found')\n"
                                "    items[item_id] = {'id': item_id, 'name': payload.name}\n"
                                "    return items[item_id]\n\n"
                                "@app.delete('/items/{item_id}')\n"
                                "def delete_item(item_id: int) -> dict[str, bool]:\n"
                                "    if item_id not in items:\n"
                                "        raise HTTPException(status_code=404, detail='not found')\n"
                                "    del items[item_id]\n"
                                "    return {'ok': True}\n"
                            ),
                        },
                        "reason": "create CRUD service",
                    },
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_2",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/tests/test_main.py",
                            "content": (
                                "from pathlib import Path\n"
                                "import importlib.util\n"
                                "from fastapi.testclient import TestClient\n\n"
                                "def _load_app_module():\n"
                                f"    p = Path('{base}/app/main.py')\n"
                                "    spec = importlib.util.spec_from_file_location('workspace_crud_app', p)\n"
                                "    module = importlib.util.module_from_spec(spec)\n"
                                "    assert spec is not None and spec.loader is not None\n"
                                "    spec.loader.exec_module(module)\n"
                                "    return module\n\n"
                                "def test_crud_flow() -> None:\n"
                                "    mod = _load_app_module()\n"
                                "    client = TestClient(mod.app)\n"
                                "    r_health = client.get('/health')\n"
                                "    assert r_health.status_code == 200\n"
                                "    assert r_health.json() == {'status': 'ok'}\n"
                                "    r_create = client.post('/items', json={'name': 'alpha'})\n"
                                "    assert r_create.status_code == 200\n"
                                "    item = r_create.json()\n"
                                "    item_id = int(item['id'])\n"
                                "    r_get = client.get(f'/items/{item_id}')\n"
                                "    assert r_get.status_code == 200\n"
                                "    assert r_get.json()['name'] == 'alpha'\n"
                                "    r_update = client.put(f'/items/{item_id}', json={'name': 'beta'})\n"
                                "    assert r_update.status_code == 200\n"
                                "    assert r_update.json()['name'] == 'beta'\n"
                                "    r_delete = client.delete(f'/items/{item_id}')\n"
                                "    assert r_delete.status_code == 200\n"
                                "    assert r_delete.json() == {'ok': True}\n"
                            ),
                        },
                        "reason": "add CRUD behavior tests",
                    },
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_3",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/README.md",
                            "content": (
                                "# Service Bootstrap App\n\n"
                                "Run:\n"
                                "1. pip install fastapi uvicorn pytest\n"
                                "2. uvicorn app.main:app --reload\n"
                                "3. curl http://127.0.0.1:8000/health\n"
                                "4. python3 -m pytest tests -q\n"
                            ),
                        },
                        "reason": "write run instructions",
                    },
                ]
            if persona_id == "qa_test":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "workspace_run",
                        "args": {"cmd": f"python3 -m pytest {base}/tests -q", "timeout_s": 60},
                        "reason": "run CRUD tests",
                    }
                ]
            return []
        if workflow_goal == "weather_station_app":
            if persona_id == "research":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "search_local_kb",
                        "args": {"query": "weather station app fastapi", "max_hits": 10},
                        "reason": "collect weather-app references",
                    }
                ]
            if persona_id == "implementation":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/app/main.py",
                            "content": (
                                "from fastapi import FastAPI\n\n"
                                "app = FastAPI(title='Weather Station')\n\n"
                                "@app.get('/weather')\n"
                                "def weather(city: str = 'San Francisco') -> dict[str, str | int]:\n"
                                "    # Local stub data; replace with real API integration later.\n"
                                "    return {'city': city, 'condition': 'sunny', 'temperature_c': 22}\n"
                            ),
                        },
                        "reason": "create weather service",
                    },
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_2",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/tests/test_weather.py",
                            "content": (
                                "from pathlib import Path\n"
                                "import importlib.util\n"
                                "from fastapi.testclient import TestClient\n\n"
                                "def _load_app_module():\n"
                                f"    p = Path('{base}/app/main.py')\n"
                                "    spec = importlib.util.spec_from_file_location('workspace_weather_app', p)\n"
                                "    module = importlib.util.module_from_spec(spec)\n"
                                "    assert spec is not None and spec.loader is not None\n"
                                "    spec.loader.exec_module(module)\n"
                                "    return module\n\n"
                                "def test_weather_endpoint_behavior() -> None:\n"
                                "    mod = _load_app_module()\n"
                                "    client = TestClient(mod.app)\n"
                                "    resp = client.get('/weather', params={'city': 'Boston'})\n"
                                "    assert resp.status_code == 200\n"
                                "    payload = resp.json()\n"
                                "    assert payload['city'] == 'Boston'\n"
                                "    assert isinstance(payload['temperature_c'], int)\n"
                            ),
                        },
                        "reason": "add regression test",
                    },
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_3",
                        "tool": "write_file",
                        "args": {
                            "path": f"{base}/README.md",
                            "content": (
                                "# Weather Station App\n\n"
                                "Run:\n"
                                "1. pip install fastapi uvicorn\n"
                                "2. uvicorn app.main:app --reload\n"
                                "3. curl 'http://127.0.0.1:8000/weather?city=Boston'\n"
                            ),
                        },
                        "reason": "write run instructions",
                    },
                ]
            if persona_id == "qa_test":
                return [
                    {
                        "tool_call_id": f"tc_{run_id[-6:]}_1",
                        "tool": "workspace_run",
                        "args": {"cmd": f"python3 -m pytest {base}/tests -q", "timeout_s": 60},
                        "reason": "run workspace tests",
                    }
                ]
            return []
        if persona_id == "research":
            return [
                {
                    "tool_call_id": f"tc_{run_id[-6:]}_1",
                    "tool": "search_local_kb",
                    "args": {"query": "fastapi hello service", "max_hits": 10},
                    "reason": "collect local references",
                }
            ]
        if persona_id == "implementation":
            return [
                {
                    "tool_call_id": f"tc_{run_id[-6:]}_1",
                    "tool": "write_file",
                    "args": {
                        "path": f"{base}/app/main.py",
                        "content": (
                            "from fastapi import FastAPI\n\n"
                            "app = FastAPI()\n\n"
                            "@app.get('/hello')\n"
                            "def hello() -> dict[str, str]:\n"
                            "    return {'message': 'hello'}\n"
                        ),
                    },
                    "reason": "create service",
                },
                {
                    "tool_call_id": f"tc_{run_id[-6:]}_2",
                    "tool": "write_file",
                    "args": {
                        "path": f"{base}/tests/test_main.py",
                        "content": (
                            "from pathlib import Path\n"
                            "import importlib.util\n"
                            "from fastapi.testclient import TestClient\n\n"
                            "def _load_app_module():\n"
                            f"    p = Path('{base}/app/main.py')\n"
                            "    spec = importlib.util.spec_from_file_location('workspace_hello_app', p)\n"
                            "    module = importlib.util.module_from_spec(spec)\n"
                            "    assert spec is not None and spec.loader is not None\n"
                            "    spec.loader.exec_module(module)\n"
                            "    return module\n\n"
                            "def test_hello_endpoint_behavior() -> None:\n"
                            "    mod = _load_app_module()\n"
                            "    client = TestClient(mod.app)\n"
                            "    resp = client.get('/hello')\n"
                            "    assert resp.status_code == 200\n"
                            "    assert resp.json() == {'message': 'hello'}\n"
                        ),
                    },
                    "reason": "create regression test",
                },
                {
                    "tool_call_id": f"tc_{run_id[-6:]}_3",
                    "tool": "write_file",
                    "args": {
                        "path": f"{base}/README.md",
                        "content": (
                            "# Hello FastAPI Service\n\n"
                            "Run:\n"
                            "1. pip install fastapi uvicorn\n"
                            "2. uvicorn app.main:app --reload\n"
                            "3. curl http://127.0.0.1:8000/hello\n"
                        ),
                    },
                    "reason": "write run instructions",
                },
            ]
        if persona_id == "qa_test":
            return [
                {
                    "tool_call_id": f"tc_{run_id[-6:]}_1",
                    "tool": "workspace_run",
                    "args": {"cmd": f"python3 -m pytest {base}/tests -q", "timeout_s": 60},
                    "reason": "run workspace tests",
                }
            ]
        return []

    def run(self, *, run_id: str, dna: DNA, snapshot: Snapshot) -> dict[str, Any]:
        backend = self._backend_for_snapshot(snapshot)
        prompt_payload = build_executor_prompt(dna, snapshot)
        prompt = render_prompt_payload(prompt_payload)
        prompt_template_id = executor_prompt_template_id(dna)
        prompt_hash = executor_prompt_hash(prompt_payload)
        violation = detect_survival_awareness_violation(prompt)
        if violation is not None:
            output = {
                "executor_output_id": f"xo_{run_id[-12:]}",
                "run_id": run_id,
                "response": {"type": "refusal", "content": "I can't comply with that framing."},
                "plan": [{"step": 1, "intent": "blocked_by_firewall"}],
                "tool_calls": [],
                "trace": {
                    "summary": "Executor blocked by reward firewall before inference",
                    "signals": {"uncertainty": "high", "assumptions": ["firewall pre-infer block"]},
                },
                "runtime": {
                    "backend": "firewall",
                    "model": "n/a",
                    "model_fingerprint": "",
                    "inference_params": {},
                    "token_counts": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "context_truncated": False,
                    "truncation_reason": None,
                    "error": {
                        "error_type": "FIREWALL_VIOLATION",
                        "stage": "pre_infer",
                        "reason_code": violation["reason_code"],
                        "offending_signal": {
                            "rule_id": violation["rule_id"],
                            "offending_span_hash": violation["offending_span_hash"],
                            "match_features": violation["match_features"],
                        },
                    },
                },
            }
            output["executor_prompt_template_id"] = prompt_template_id
            output["executor_prompt_hash"] = prompt_hash
            ensure_dream_grid(output)
            return output
        assert_no_forbidden_executor_payload(prompt_payload.__dict__)
        assert_no_survival_awareness(prompt)
        output = backend.generate(run_id=run_id, dna=dna, snapshot=snapshot)
        self._maybe_parse_model_contract(output)
        runtime = output.get("runtime")
        runtime_error = runtime.get("error") if isinstance(runtime, dict) else None
        fallback_env = os.getenv("ALLOW_DETERMINISTIC_TOOL_FALLBACK", "").strip().lower()
        allow_fallback = fallback_env in {"1", "true", "yes", "on"}
        if isinstance(backend, MockBackend):
            allow_fallback = True
        workflow_goal = str(((snapshot.context.state or {}).get("workflow_goal") if snapshot.context else "") or "")
        force_workflow_fallback = workflow_goal in {
            "hello_fastapi_service",
            "weather_station_app",
            "generic_build_app",
            "service_bootstrap_app",
            "ui_page_update",
        }
        if not output.get("tool_calls") and runtime_error is None and (allow_fallback or force_workflow_fallback):
            deterministic_calls = self._workflow_tool_calls(run_id=run_id, dna=dna, snapshot=snapshot)
            if deterministic_calls:
                output["tool_calls"] = deterministic_calls
        response = output.get("response")
        if isinstance(response, dict):
            raw_content = response.get("content")
            if isinstance(raw_content, str):
                cleaned = normalize_response_content(raw_content)
                response["content_raw"] = raw_content
                response["content"] = cleaned
        output["executor_prompt_template_id"] = prompt_template_id
        output["executor_prompt_hash"] = prompt_hash
        ensure_dream_grid(output)
        assert_tool_intents(output.get("tool_calls", []))
        return output
