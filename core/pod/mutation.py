from __future__ import annotations

from core.pod.dna import DNA, make_dna_id, utc_now_iso


def mutate_dna(parent: DNA, mutation_id: str) -> DNA:
    # v1 controlled mutation: toggle a single safe knob (style.verbosity).
    next_verbosity = "high" if parent.style.get("verbosity") == "low" else "low"
    new_style = dict(parent.style)
    new_style["verbosity"] = next_verbosity
    new_constraints = list(parent.system_constraints)
    prompt_template = dict(parent.prompt_template)

    return DNA(
        dna_id=make_dna_id(prompt_template, new_constraints, new_style),
        version=parent.version + 1,
        created_at=utc_now_iso(),
        persona=parent.persona,
        system_constraints=new_constraints,
        style=new_style,
        prompt_template=prompt_template,
        tool_policy_overrides=parent.tool_policy_overrides,
        lineage={"parents": [parent.dna_id], "mutation_id": mutation_id},
    )


def propose_mutations(dna_pool: list[DNA]) -> list[DNA]:
    candidates: list[DNA] = []
    for idx, dna in enumerate(dna_pool):
        candidates.append(mutate_dna(dna, mutation_id=f"mut_{idx + 1}"))
    return candidates
