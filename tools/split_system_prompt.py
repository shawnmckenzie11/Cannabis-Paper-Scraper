#!/usr/bin/env python3
"""Split monolithic system_prompt into node-linked prompt sections (content-preserving)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = REPO_ROOT / "rules_config.json"


def _load_legacy_system_prompt() -> str:
    """Loads the pre-split system_prompt from git HEAD or the current config fallback."""
    try:
        raw = subprocess.check_output(
            ["git", "show", "HEAD:rules_config.json"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        legacy = json.loads(raw).get("system_prompt")
        if isinstance(legacy, str) and legacy.strip():
            return legacy
    except Exception:
        pass

    if RULES_PATH.exists():
        current = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        legacy = current.get("system_prompt")
        if isinstance(legacy, str) and legacy.strip():
            return legacy
    raise RuntimeError("Could not locate legacy system_prompt to split.")


def _section_between(text: str, start: str, end: Optional[str] = None) -> str:
    """Returns text between markdown headers."""
    if start not in text:
        return ""
    chunk = text.split(start, 1)[1]
    if end and end in chunk:
        chunk = chunk.split(end, 1)[0]
    return start + chunk.strip()


def _subsection(text: str, header: str) -> str:
    """Returns a ### subsection including its header."""
    pattern = rf"(### {re.escape(header)}[\s\S]*?)(?=\n### |\n## |\Z)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _subsections_matching(text: str, prefix: str) -> str:
    """Concatenates ### subsections whose header starts with prefix."""
    blocks = []
    for match in re.finditer(r"(### [^\n]+[\s\S]*?)(?=\n### |\n## |\Z)", text):
        block = match.group(1).strip()
        header = block.split("\n", 1)[0]
        if header.startswith(f"### {prefix}"):
            blocks.append(block)
    return "\n\n".join(blocks)


def _study_type_blocks(fields_text: str) -> Dict[str, str]:
    """Splits study_type enum blocks by expert branch."""
    study = _subsection(fields_text, "2. study_type (list of strings, multi-label)")
    if not study:
        return {}

    def grab(label: str) -> str:
        pattern = rf"(\*\*{re.escape(label)}:\*\*[\s\S]*?)(?=\n\*\*|\n### |\Z)"
        match = re.search(pattern, study)
        return match.group(1).strip() if match else ""

    return {
        "clinical": grab("Clinical/Human"),
        "animal": grab("Animal Models (in vivo)"),
        "vitro": grab("Cell Culture (in vitro)"),
        "review": grab("Review types"),
    }


def build_prompt_sections(legacy_prompt: str) -> Dict[str, Any]:
    """Builds split prompt config from the legacy monolithic system_prompt."""
    preamble = legacy_prompt.split("## Fields to extract", 1)[0].strip()
    fields_text = _section_between(legacy_prompt, "## Fields to extract", "## Output format")
    output_format = _section_between(legacy_prompt, "## Output format", "## Important rules")
    important_rules = _section_between(legacy_prompt, "## Important rules")

    pub_type = _subsection(fields_text, "1. publication_type (single string)")
    study_blocks = _study_type_blocks(fields_text)
    exposure = _subsection(fields_text, "3. exposure_method (list of strings, multi-label)")
    cannabis_type = _subsection(fields_text, "4. cannabis_type (list of strings, multi-label)")
    outcome = _subsection(fields_text, "5. outcome_domain (list of strings, multi-label)")
    concentrations = _subsection(fields_text, "6. Cannabinoid concentrations")
    strain = _subsection(fields_text, "7. Strain/Chemotype")
    timing = _subsection(fields_text, "8. Timing parameters")
    sample_flags = _subsection(fields_text, "9. Sample size and design flags")
    per_unit = _subsection(fields_text, "10. Puff count and THC/CBD concentrations (per-unit)")
    micromolar = _subsection(fields_text, "11. In vitro micromolar concentrations")

    shared_fields = "\n\n".join(
        block
        for block in [
            "## Shared extraction fields (apply after node routing)",
            cannabis_type,
            outcome,
            concentrations,
            strain,
            timing,
            sample_flags,
            per_unit,
            micromolar,
        ]
        if block
    )

    global_rules = important_rules
    for node_specific in (
        "**Context-Specific Rules for Clinical vs Preclinical vs In Vitro contexts**:",
        "**Optimal Identification and Classification of Original Research with Dose & Strain**:",
        "**Relevance and 'not cannabis-related' Classification**:",
    ):
        global_rules = global_rules.replace(node_specific, "").strip()

    global_rules = "\n\n".join(
        part
        for part in [
            "## Global extraction rules",
            "\n".join(
                line
                for line in global_rules.splitlines()
                if line.strip()
                and not line.strip().startswith("* **Clinical")
                and not line.strip().startswith("* **Preclinical")
                and not line.strip().startswith("* **Cell Culture")
                and not line.strip().startswith("* Ensure the distinction")
                and not line.strip().startswith("* For all \"original research\"")
                and not line.strip().startswith("* If multiple dose levels")
                and not line.strip().startswith("* If a paper discusses GPR55")
                and not line.strip().startswith("* For example, the paper")
            ),
        ]
        if part.strip()
    )

    relevance_block = ""
    if "**Relevance and 'not cannabis-related' Classification**:" in important_rules:
        relevance_block = _section_between(
            important_rules + "\n## END",
            "**Relevance and 'not cannabis-related' Classification**:",
            "## END",
        ).replace("## END", "").strip()

    clinical_rules = ""
    preclinical_rules = ""
    vitro_rules = ""
    if "**Context-Specific Rules for Clinical vs Preclinical vs In Vitro contexts**:" in important_rules:
        ctx = _section_between(
            important_rules + "\n## END",
            "**Context-Specific Rules for Clinical vs Preclinical vs In Vitro contexts**:",
            "- **Optimal Identification",
        )
        clinical_rules = _section_between(ctx + "\n## END", "* **Clinical (Human) Context**:", "* **Preclinical")
        preclinical_rules = _section_between(ctx + "\n## END", "* **Preclinical Animal (In Vivo) Context**:", "* **Cell Culture")
        vitro_rules = _section_between(ctx + "\n## END", "* **Cell Culture (In Vitro) Context**:", "## END").replace("## END", "")

    original_research_rules = ""
    if "**Optimal Identification and Classification of Original Research with Dose & Strain**:" in important_rules:
        original_research_rules = _section_between(
            important_rules + "\n## END",
            "**Optimal Identification and Classification of Original Research with Dose & Strain**:",
            "- **Relevance",
        ).strip()

    def exposure_branch(label: str) -> str:
        pattern = rf"(\*\*{re.escape(label)}:\*\*[\s\S]*?)(?=\n\*\*|\nIf none apply|\n### |\Z)"
        match = re.search(pattern, exposure)
        return match.group(1).strip() if match else ""

    system_prompt_base = (
        preamble
        + "\n\nRoute using the expert decision tree node sections below in order: "
        "Node 0 → Node 1B/1C before Node 1A → Node 2 (clinical / in vivo / in vitro / mixed) "
        "or Node 3 review subtypes."
    )

    decision_nodes: Dict[str, Any] = {
        "node0_ingestion": {
            "order": 0,
            "tree_label": "Node 0 · Ingestion",
            "purpose": "Decide whether title/abstract warrant full classification (relevant vs tangential vs irrelevant).",
            "outputs": ["relevant", "tangential", "irrelevant", "not cannabis-related"],
            "positive_cues": [
                "cannabis", "marijuana", "cannabinoid", "THC", "CBD", "endocannabinoid",
                "administration", "pharmacology", "clinical outcome",
            ],
            "negative_cues": [
                "hemp fiber", "agricultural yield", "textile",
                "taxonomy-only", "legal/policy-only", "unrelated acronym collisions",
            ],
            "prompt_section": "\n\n".join(
                block
                for block in [
                    "## Node 0: Ingestion / Relevance gate",
                    pub_type,
                    relevance_block,
                ]
                if block
            ),
        },
        "node1b_reviews": {
            "order": 10,
            "tree_label": "Node 1B · Reviews / Secondary Literature",
            "purpose": "Route reviews, systematic reviews, meta-analyses, editorials, comments, letters, and perspectives BEFORE original-research extraction.",
            "route_before": "node1a_original",
            "positive_cues": [
                "review", "systematic review", "meta-analysis", "narrative synthesis",
                "scoping review", "editorial", "commentary", "letter to the editor", "perspectives",
            ],
            "negative_cues": [
                "we conducted", "participants were randomized", "n=", "Methods section with new experiments",
            ],
            "prompt_section": "\n\n".join(
                block
                for block in [
                    "## Node 1B: Reviews / Secondary Literature (route BEFORE Node 1A)",
                    "From publication_type, apply: review, systematic review, meta-analysis, editorial, comment, letter to the editor, perspectives paper.",
                    study_blocks.get("review", ""),
                    "Do NOT extract animal strains, cell lines, supplier details, or dose values from cited studies unless describing the review methodology itself.",
                ]
                if block
            ),
        },
        "node1c_case_report": {
            "order": 15,
            "tree_label": "Node 1C · Case Report",
            "purpose": "Detailed report of one or a few cases.",
            "route_before": "node1a_original",
            "positive_cues": ["case report", "case series", "we report a case", "single patient"],
            "negative_cues": ["randomized", "cohort of", "n=", "systematic search"],
            "prompt_section": "## Node 1C: Case Report\n"
            + "publication_type: case study. study_type: case study. "
            + "Extract exposure/dose only for the reported case(s), not literature cited in discussion.",
        },
        "node1a_original": {
            "order": 20,
            "tree_label": "Node 1A · Original Papers",
            "purpose": "Papers with primary data, new results, or experimental/clinical observations.",
            "route_after": ["node1b_reviews", "node1c_case_report"],
            "positive_cues": [
                "Methods", "Results", "in vivo", "in vitro", "exposure", "participants",
                "cohort", "sample size", "case-control", "animal experiment", "cell line assay",
                "intervention arm", "dose",
            ],
            "negative_cues": [
                "we review", "literature suggests", "previous studies have shown", "summarize evidence",
            ],
            "prompt_section": "\n\n".join(
                block
                for block in [
                    "## Node 1A: Original Research",
                    'publication_type: "original research" — primary research presenting new data/experiments.',
                    original_research_rules,
                ]
                if block
            ),
        },
        "node2a_clinical": {
            "order": 30,
            "tree_label": "Node 2A · Clinical",
            "parent": "node1a_original",
            "purpose": "Human subjects: interventional and observational designs.",
            "positive_cues": [
                "participants", "patients", "randomized", "placebo", "trial", "cohort",
                "adverse events", "dose", "product form", "route of administration",
            ],
            "negative_cues": [
                "cell culture", "primary neurons", "mouse", "rat", "organoid", "assay plate",
            ],
            "sub_branches": {
                "interventional": ["Clinical (RCT)", "non-randomized trial"],
                "observational": [
                    "Clinical (prospective)", "Clinical (retrospective)", "Clinical (observational)",
                ],
            },
            "prompt_section": "\n\n".join(
                block
                for block in [
                    "## Node 2A: Original → Clinical",
                    study_blocks.get("clinical", ""),
                    exposure_branch("Clinical/Human routes"),
                    clinical_rules,
                ]
                if block
            ),
        },
        "node2b_in_vivo": {
            "order": 31,
            "tree_label": "Node 2B · In Vivo",
            "parent": "node1a_original",
            "purpose": "Animal whole-organism experiments.",
            "positive_cues": [
                "mouse", "mice", "rat", "hamster", "oral gavage", "intraperitoneal",
                "behavior test", "tissue analysis", "sacrificed animals",
            ],
            "negative_cues": [
                "human participant", "clinical trial", "patient-reported outcome", "chart review",
            ],
            "sub_branches": {
                "rodent": ["Animal Models (Mouse)", "Animal Models (Rat)", "Animal Models (Other Rodents)"],
                "non_rodent_mammal": ["Animal Models (Non-Human Primates)", "Animal Models (Other)"],
                "non_mammal": ["Animal Models (Other)"],
            },
            "prompt_section": "\n\n".join(
                block
                for block in [
                    "## Node 2B: Original → In Vivo",
                    study_blocks.get("animal", ""),
                    exposure_branch("In vivo (animal) methods"),
                    preclinical_rules,
                ]
                if block
            ),
        },
        "node2c_in_vitro": {
            "order": 32,
            "tree_label": "Node 2C · In Vitro",
            "parent": "node1a_original",
            "purpose": "Cell-based, biochemical, receptor, and assay studies.",
            "positive_cues": [
                "cell line", "primary cells", "immortalized cells", "cultured", "incubated",
                "concentration", "receptor binding", "assay", "ELISA", "microglia", "THP-1", "A549",
            ],
            "negative_cues": [
                "patient", "participant", "trial", "animal dosing", "behavioral endpoint",
            ],
            "sub_branches": {
                "immortalized_cell": ["Cell Culture (Cell Lines)"],
                "primary_cell": ["Cell Culture (Primary Cells)"],
                "co_culture": ["Cell Culture (Co-Culture)"],
                "organoid": ["Cell Culture (Organoids)"],
            },
            "prompt_section": "\n\n".join(
                block
                for block in [
                    "## Node 2C: Original → In Vitro",
                    study_blocks.get("vitro", ""),
                    exposure_branch("In vitro methods"),
                    vitro_rules,
                ]
                if block
            ),
        },
        "node2d_mixed": {
            "order": 33,
            "tree_label": "Node 2D · Mixed / Unclear",
            "parent": "node1a_original",
            "purpose": "Papers combining multiple study types.",
            "prompt_section": "## Node 2D: Original → Mixed / Unclear\n"
            "When clinical, in vivo, and in vitro labels all apply, include all applicable study_type values "
            "and apply branch-specific strain/dose rules per model section.",
        },
        "node3a_systematic_review": {
            "order": 40,
            "tree_label": "Node 3A · Systematic Review",
            "parent": "node1b_reviews",
            "purpose": "Structured, replicable literature reviews.",
            "positive_cues": ["systematic review", "structured search", "PRISMA", "included studies"],
            "prompt_section": "## Node 3A: Review → Systematic Review\n"
            "publication_type: systematic review. Positive cues: structured search, PRISMA, included studies.",
        },
        "node3b_meta_analysis": {
            "order": 41,
            "tree_label": "Node 3B · Meta-analysis",
            "parent": "node1b_reviews",
            "purpose": "Statistical pooling of multiple studies.",
            "positive_cues": ["meta-analysis", "pooled estimate", "forest plot"],
            "prompt_section": "## Node 3B: Review → Meta-analysis\n"
            "publication_type: meta-analysis. study_type: meta-analysis, review.",
        },
        "node3c_narrative_editorial": {
            "order": 42,
            "tree_label": "Node 3C · Narrative / Editorial / Comment",
            "parent": "node1b_reviews",
            "purpose": "Narrative reviews, editorials, commentary, letters, perspectives.",
            "positive_cues": ["narrative review", "editorial", "commentary", "perspective"],
            "prompt_section": "## Node 3C: Review → Narrative / Editorial / Comment / Letter\n"
            "publication_type: review, editorial, comment, letter to the editor, perspectives paper.",
        },
    }

    return {
        "system_prompt_base": system_prompt_base,
        "prompt_sections": {
            "shared_fields": shared_fields,
            "output_format": output_format,
            "global_rules": global_rules,
        },
        "decision_nodes": decision_nodes,
    }


def main() -> None:
    """Rewrites rules_config.json with content-preserving node-linked prompt sections."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    import classifier

    legacy_prompt = _load_legacy_system_prompt()
    sections = build_prompt_sections(legacy_prompt)

    with open(RULES_PATH, encoding="utf-8") as handle:
        config = json.load(handle)

    config.pop("system_prompt", None)
    config.update(sections)
    config["version"] = "2.4.0"
    agent = config.setdefault("agent_automation", {})
    agent["decision_chart_status"] = (
        "expert decision tree v2026-06-18 — system_prompt split into node-linked prompt_section blocks"
    )
    agent["decision_tree_source"] = "EXPERT FEEDBACK TREE & CUES + Decision Tree.pdf (2026-06-18)"

    with open(RULES_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    prompt = classifier.compile_system_prompt(config)
    print(f"Updated {RULES_PATH} to v{config['version']} with {len(sections['decision_nodes'])} nodes.")
    print(f"Compiled prompt length: {len(prompt)} chars")


if __name__ == "__main__":
    main()
