from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from core.observability.canonical import canonical_sha256
from core.pod.dna import DNA
from core.snapshot.schema import Snapshot


OUTPUT_CONTRACT = {
    "executor_output_schema": {
        "type": "object",
        "required": ["response", "tool_calls"],
        "properties": {"response": "object", "tool_calls": "array"},
    },
    "response_schema": {
        "type": "object",
        "required": ["type", "content"],
        "properties": {"type": "final|partial|refusal", "content": "string", "structured": "object?"},
    },
    "tool_call_schema": {
        "type": "object",
        "required": ["tool_call_id", "tool", "args"],
        "properties": {"tool_call_id": "string", "tool": "string", "args": "object", "reason": "string?"},
    },
}


@dataclass
class PromptPayload:
    system: str
    instructions: str
    system_constraints: List[str]
    style: Dict[str, str]
    request: Dict[str, Any]
    context: Dict[str, Any]
    policies: Dict[str, Any]
    output_contract: Dict[str, Any]

    def to_prompt_text(self) -> str:
        state = self.context.get("state") if isinstance(self.context, dict) else {}
        required_tools = state.get("required_tools") if isinstance(state, dict) else None
        tool_hint = state.get("tool_call_contract_hint") if isinstance(state, dict) else None
        arg_hint = state.get("tool_arg_contract_hint") if isinstance(state, dict) else None
        repair_instruction = state.get("repair_instruction") if isinstance(state, dict) else None
        workflow_goal = state.get("workflow_goal") if isinstance(state, dict) else None
        persona_id = state.get("persona_id") if isinstance(state, dict) else None
        workspace_id = state.get("workspace_id") if isinstance(state, dict) else None
        extra_directives: list[str] = []
        if workflow_goal:
            extra_directives.append(f"WORKFLOW_GOAL={workflow_goal}")
        if persona_id:
            extra_directives.append(f"PERSONA_ID={persona_id}")
        if isinstance(required_tools, list) and required_tools:
            req = ", ".join(str(t) for t in required_tools if str(t).strip())
            extra_directives.append(f"REQUIRED_TOOLS={req}")
            extra_directives.append("If required tools are provided, prioritize valid tool_calls over explanatory prose.")
        if isinstance(tool_hint, str) and tool_hint.strip():
            extra_directives.append(tool_hint.strip())
        if isinstance(arg_hint, str) and arg_hint.strip():
            extra_directives.append(arg_hint.strip())
        if (
            isinstance(workspace_id, str)
            and workspace_id.strip()
            and isinstance(required_tools, list)
            and "write_file" in [str(t).strip() for t in required_tools]
        ):
            ws = workspace_id.strip()
            extra_directives.append(
                f"For write_file, path MUST start with data/workspaces/{ws}/"
            )
        if isinstance(repair_instruction, str) and repair_instruction.strip():
            extra_directives.append(f"REPAIR: {repair_instruction.strip()}")
        directives_block = f"\nWORKFLOW_DIRECTIVES: {' | '.join(extra_directives)}" if extra_directives else ""
        return (
            f"SYSTEM: {self.system}\n"
            f"CONSTRAINTS: {' | '.join(self.system_constraints)}\n"
            f"STYLE: {self.style}\n"
            f"INSTRUCTIONS: {self.instructions}\n"
            f"REQUEST: {self.request}\n"
            f"CONTEXT: {self.context}\n"
            f"POLICIES: {self.policies}\n"
            f"OUTPUT_CONTRACT: {self.output_contract}\n"
            f"{directives_block}\n"
            "RESPONSE_FORMAT: Return only one JSON object with keys "
            "\"response\" and \"tool_calls\". No markdown. "
            "Do not echo schema or contract fields inside response.content."
        )


def build_executor_prompt(dna: DNA, snapshot: Snapshot) -> PromptPayload:
    # Whitelist-only assembly. No judge/selector/router state enters this payload.
    request_payload = snapshot.request.model_dump()
    context_payload = snapshot.context.model_dump()
    policies_payload = snapshot.policies.model_dump()
    return PromptPayload(
        system=dna.prompt_template["system"],
        instructions=dna.prompt_template["instructions"],
        system_constraints=list(dna.system_constraints),
        style=dict(dna.style),
        request=request_payload,
        context=context_payload,
        policies=policies_payload,
        output_contract=OUTPUT_CONTRACT,
    )


def render_executor_prompt(dna: DNA, snapshot: Snapshot) -> str:
    payload = build_executor_prompt(dna, snapshot)
    return payload.to_prompt_text()


def render_prompt_payload(payload: PromptPayload) -> str:
    return payload.to_prompt_text()


def executor_prompt_template_id(dna: DNA) -> str:
    template_payload = {
        "system": dna.prompt_template.get("system", ""),
        "instructions": dna.prompt_template.get("instructions", ""),
        "system_constraints": list(dna.system_constraints),
        "style": dict(dna.style),
        "output_contract": OUTPUT_CONTRACT,
    }
    return f"ept_{canonical_sha256(template_payload)[:12]}"


def executor_prompt_hash(payload: PromptPayload) -> str:
    return f"h_{canonical_sha256(payload.to_prompt_text())[:16]}"
