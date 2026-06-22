#!/usr/bin/env python3
"""Quick count of llm-pdf-reclassify papers with UI filter parity."""

from db_manager import DatabaseManager

db = DatabaseManager()
c = db.get_connection().cursor()

c.execute(
    "SELECT COUNT(*) FROM papers WHERE classifier_version LIKE ?",
    ("llm-pdf-reclassify-%",),
)
print("llm_pdf_total", c.fetchone()[0])

where, params = db._build_filter_clauses({"classification_level": "claude_pdf"})
c.execute("SELECT COUNT(*) FROM papers WHERE " + " AND ".join(where), params)
print("ui_claude_pdf_filter", c.fetchone()[0])

where2, params2 = db._build_filter_clauses({"classification_level": "claude_pdf", "tab": "clinical"})
c.execute("SELECT COUNT(*) FROM papers WHERE " + " AND ".join(where2), params2)
print("ui_claude_pdf_clinical_tab", c.fetchone()[0])

where3, params3 = db._build_filter_clauses({"classification_level": "claude_pdf", "tab": "preclinical"})
c.execute("SELECT COUNT(*) FROM papers WHERE " + " AND ".join(where3), params3)
print("ui_claude_pdf_preclinical_tab", c.fetchone()[0])
