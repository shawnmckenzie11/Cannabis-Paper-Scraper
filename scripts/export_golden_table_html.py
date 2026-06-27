#!/usr/bin/env python3
"""Export golden dataset as browsable HTML table with paper/PDF links."""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import golden_dataset_paths

INPUT = ROOT / "scratch/golden_dataset/tree_path_golden.json"
OUTPUT = ROOT / "scratch/golden_dataset/tree_path_golden_table.html"
STATUS_PATH = ROOT / "scratch/golden_dataset/golden_endpoint_status.json"


def _link_cell(url: str, label: str | None = None) -> str:
    """Returns an HTML anchor or em dash when URL is empty."""
    if not url:
        return "—"
    text = label or url
    if len(text) > 60:
        text = text[:57] + "..."
    return f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(text)}</a>'


def _format_label_list(values: list | tuple | None) -> str:
    """Joins endpoint path labels for display in summary table cells."""
    if not values:
        return "—"
    items = [str(item).strip() for item in values if str(item).strip()]
    return ", ".join(items) if items else "—"


def _endpoint_characteristics_cells(endpoint: dict) -> str:
    """Formats study type, exposure, gates, and scored characteristic fields."""
    study = escape(_format_label_list(endpoint.get("study_types")))
    exposure = escape(_format_label_list(endpoint.get("exposure_methods")))
    required = endpoint.get("required_gate_fields") or []
    scored = endpoint.get("scored_fields") or endpoint.get("scope_fields") or []
    required_txt = escape(", ".join(str(field) for field in required)) if required else "—"
    scored_txt = escape(", ".join(str(field) for field in scored)) if scored else "—"
    return (
        f"<td>{study}</td><td>{exposure}</td>"
        f"<td>{required_txt}</td><td>{scored_txt}</td>"
    )


def _rl_status_cell(endpoint_id: str, status_map: dict) -> str:
    """Formats RL cycle summary columns for one endpoint row."""
    st = status_map.get(endpoint_id) or {}
    if not st:
        return (
            "<td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>"
        )
    guard_pct = st.get("batch_alignment_pct")
    guard_txt = f"{guard_pct}%" if guard_pct is not None else "—"
    if st.get("guard_passed"):
        guard_txt = f"✓ {guard_txt}"
    promoted = st.get("promoted_count")
    promoted_ids = st.get("promoted_paper_ids") or []
    promoted_txt = str(promoted) if promoted is not None else "—"
    if promoted_ids:
        promoted_txt = f"{promoted_txt} ({', '.join(str(p) for p in promoted_ids[:5])})"
    push_txt = st.get("push_summary") or "—"
    build_txt = escape(str(st.get("maude_build_id") or "—"))
    status_txt = escape(str(st.get("status") or "—"))
    return (
        f"<td>{status_txt}</td>"
        f"<td>{escape(guard_txt)}</td>"
        f"<td>{escape(promoted_txt)}</td>"
        f"<td>{escape(str(push_txt))}</td>"
        f"<td>{build_txt}</td>"
    )


def main() -> None:
    """Writes the HTML breakdown table."""
    with open(INPUT, encoding="utf-8") as handle:
        data = json.load(handle)

    status_data: dict = {}
    if STATUS_PATH.is_file():
        with open(STATUS_PATH, encoding="utf-8") as handle:
            status_data = json.load(handle).get("endpoints") or {}

    rows_html: list[str] = []
    for ep in data["endpoints"]:
        for rank, paper in enumerate(ep.get("papers") or [], 1):
            pmid = paper.get("pmid")
            pubmed = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
            full_text = paper.get("full_text_link") or ""
            doi = paper.get("doi") or ""
            doi_url = (
                f"https://doi.org/{doi}"
                if doi and not str(doi).startswith("http")
                else (doi if str(doi).startswith("http") else "")
            )
            pdf_label = "PDF" if paper.get("has_pdf_link") else "Full-text"
            title = escape((paper.get("title") or "")[:100])
            rows_html.append(
                f"<tr>"
                f"<td>{escape(ep['endpoint_id'])}</td>"
                f"<td>{escape(ep['label'])}</td>"
                f"<td>{escape(ep['branch'])}</td>"
                f"<td>{rank}</td>"
                f"<td>{paper.get('paper_id')}</td>"
                f"<td>{escape(str(pmid or '—'))}</td>"
                f"<td>{escape(str(paper.get('year') or '—'))}</td>"
                f"<td>{escape(paper.get('selection_tier') or '')}</td>"
                f"<td>{paper.get('characteristics_identified_count')}/{paper.get('characteristics_in_scope')}</td>"
                f"<td>{title}</td>"
                f"<td>{_link_cell(pubmed, 'PubMed') if pubmed else '—'}</td>"
                f"<td>{_link_cell(doi_url, 'DOI') if doi_url else '—'}</td>"
                f"<td>{_link_cell(full_text, pdf_label) if full_text else '—'}</td>"
                f"</tr>"
            )

    summary_rows: list[str] = []
    sorted_endpoints = golden_dataset_paths.sort_endpoints_by_pdf_class_pool(
        data["endpoints"],
        pdf_class_target=int(data.get("top_n_per_endpoint") or 10),
    )
    for ep in sorted_endpoints:
        eid = ep["endpoint_id"]
        summary_rows.append(
            f"<tr>"
            f"<td>{escape(eid)}</td>"
            f"<td>{escape(ep['label'])}</td>"
            f"<td>{escape(ep['branch'])}</td>"
            + _endpoint_characteristics_cells(ep)
            + f"<td>{ep.get('selected_count', 0)}</td>"
            f"<td>{ep.get('pool_size_pdf_classification', 0)}</td>"
            f"<td>{ep.get('pool_size_pdf_keywords', 0)}</td>"
            + _rl_status_cell(eid, status_data)
            + "</tr>"
        )

    filter_script = """
const input = document.getElementById('filter');
const rows = document.querySelectorAll('#papers tbody tr');
input.addEventListener('input', () => {
  const q = input.value.toLowerCase();
  rows.forEach((r) => {
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
});
"""

    html = (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<title>Golden Dataset — Tree Path Breakdown</title>\n"
        "<style>\n"
        "body { font-family: system-ui, sans-serif; margin: 24px; color: #1a1a1a; }\n"
        "h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }\n"
        ".meta { color: #555; margin-bottom: 1.5rem; font-size: 0.9rem; }\n"
        "h2 { font-size: 1.1rem; margin-top: 2rem; }\n"
        "table { border-collapse: collapse; width: 100%; font-size: 0.82rem; margin-bottom: 2rem; }\n"
        "th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }\n"
        "th { background: #f4f4f4; position: sticky; top: 0; }\n"
        "tr:nth-child(even) { background: #fafafa; }\n"
        "a { color: #0b5fff; }\n"
        "#filter { width: 100%; max-width: 480px; padding: 8px; margin-bottom: 12px; font-size: 0.9rem; }\n"
        ".wrap { overflow-x: auto; }\n"
        "</style>\n</head>\n<body>\n"
        f"<h1>Golden Dataset — Complete Breakdown</h1>\n"
        f"<p class=\"meta\">Generated {escape(data.get('created_at', ''))} · "
        f"{data.get('endpoint_count')} endpoints · {data.get('total_paper_selections')} paper slots · "
        f"PDF pool {data.get('pdf_paper_pool_size')}</p>\n"
        "<h2>Endpoint summary</h2>\n<div class=\"wrap\"><table>\n"
        "<thead><tr>"
        "<th>Endpoint ID</th><th>Label</th><th>Branch</th>"
        "<th>Study type</th><th>Exposure method</th><th>Required gates</th><th>Scored fields</th>"
        "<th>Selected</th>"
        "<th>Pool (PDF class)</th><th>Pool (PDF kw)</th>"
        "<th>RL status</th><th>Guard align</th><th>Promoted</th><th>Push</th><th>Maude build</th>"
        "</tr></thead>\n<tbody>"
        + "".join(summary_rows)
        + "</tbody></table></div>\n"
        f"<h2>All papers ({data.get('total_paper_selections')} rows)</h2>\n"
        "<input type=\"text\" id=\"filter\" placeholder=\"Filter by endpoint, title, pmid, tier…\" />\n"
        "<div class=\"wrap\"><table id=\"papers\">\n"
        "<thead><tr>"
        "<th>Endpoint</th><th>Label</th><th>Branch</th><th>Rank</th><th>Paper ID</th>"
        "<th>PMID</th><th>Year</th><th>Tier</th><th>Chars</th><th>Title</th>"
        "<th>PubMed</th><th>DOI</th><th>PDF / Full-text</th>"
        "</tr></thead>\n<tbody>"
        + "".join(rows_html)
        + "</tbody></table></div>\n<script>"
        + filter_script
        + "</script>\n</body></html>"
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT} ({len(rows_html)} paper rows)")


if __name__ == "__main__":
    main()
