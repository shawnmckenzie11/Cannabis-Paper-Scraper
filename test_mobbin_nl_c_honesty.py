"""Static contracts for Mobbin NL-C chart drilldown + NL-1/NL-2 honesty."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent / "templates" / "index.html"
APP_PATH = Path(__file__).resolve().parent / "app.py"

# Do-not-regress #73 Wonder Analyze chrome (verbatim)
WONDER_WAIT = "Analyzing… · you can leave this page"
WONDER_CANCEL = "Cancel analysis"
WONDER_EMPTY = "Nothing to analyze with these filters."
WONDER_ERROR = "Analysis didn’t finish. Try again."
WONDER_RETRY = "Retry"
WONDER_DONE = "Analysis ready."
WONDER_OPEN = "Open results"
WONDER_GUEST = "Sign in to save analyses."
WONDER_CAP = "Showing first "
WONDER_CAP_TAIL = "results may be incomplete."
EMPTY_SLICE = "No papers in this slice."


def _read_index() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    match = re.search(rf"function {re.escape(name)}\s*\(", html)
    if not match:
        raise AssertionError(f"function {name}() not found")
    start = match.start()
    depth = 0
    started = False
    params_end = None
    for idx in range(match.end() - 1, len(html)):
        ch = html[idx]
        if ch == "(":
            depth += 1
            started = True
        elif ch == ")":
            depth -= 1
            if started and depth == 0:
                params_end = idx
                break
    brace = html.find("{", params_end)
    depth = 0
    for idx in range(brace, len(html)):
        if html[idx] == "{":
            depth += 1
        elif html[idx] == "}":
            depth -= 1
            if depth == 0:
                return html[start : idx + 1]
    raise AssertionError(f"Could not parse {name}()")


class TestMobbinNlCHonesty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read_index()
        cls.app = APP_PATH.read_text(encoding="utf-8")

    def test_wonder_analyze_chrome_unchanged(self):
        for s in (WONDER_WAIT, WONDER_CANCEL, WONDER_EMPTY, WONDER_ERROR, WONDER_RETRY, WONDER_DONE, WONDER_OPEN, WONDER_GUEST):
            self.assertIn(s, self.html)

    def test_chart_click_paints_right_pane(self):
        self.assertIn("function handleAnalysisChartClick", self.html)
        body = _function_body(self.html, "handleAnalysisChartClick")
        self.assertIn("ensureAnalysisPapersForDrilldown", body)
        self.assertIn("renderChartDrilldownTable", body)
        self.assertIn("filterPapersForChartSegment", body)

    def test_drilldown_n_of_m_and_load_more(self):
        self.assertIn("Showing first ", _function_body(self.html, "renderChartDrilldownTable"))
        self.assertIn("function loadMoreChartDrilldown", self.html)
        self.assertIn("btn-drilldown-load-more", self.html)
        self.assertIn("drilldown-year", self.html)

    def test_empty_slice_copy_exact(self):
        self.assertIn(EMPTY_SLICE, self.html)
        self.assertIn(EMPTY_SLICE, _function_body(self.html, "renderChartDrilldownTable"))

    def test_cap_banner_wonder_copy(self):
        body = _function_body(self.html, "updateAnalysisCapBanner")
        self.assertIn(WONDER_CAP, body)
        self.assertIn(WONDER_CAP_TAIL, body)
        self.assertIn('id="analysis-cap-banner"', self.html)
        self.assertIn(WONDER_CAP, self.app)
        self.assertIn(WONDER_CAP_TAIL, self.app)

    def test_analyses_provenance_and_truncated_chip(self):
        self.assertIn("analysis-provenance", self.html)
        self.assertIn("Truncated", self.html)
        self.assertIn("formatAnalysisCreatedAt", self.html)
        self.assertIn('a["truncated"]', self.app)

    def test_section_stats_sampled_n(self):
        body = _function_body(self.html, "updateSectionStatsDisplay")
        self.assertIn("sampled (", body)
        self.assertIn("section-stats-sampled", self.html)
        self.assertIn('stats["sample_size"]', self.app)

    def test_pdf_wizard_steps(self):
        self.assertIn("1 File", self.html)
        self.assertIn("2 Match", self.html)
        self.assertIn("3 Review", self.html)
        self.assertIn("pdf-wizard-steps", self.html)

    def test_harvest_percent_not_keyword_fake(self):
        body = _function_body(self.html, "startPollingHarvestStatus")
        self.assertNotIn('includes("Counting")', body)
        self.assertNotIn('includes("Enriching")', body)
        self.assertIn("processed", body)
        self.assertIn('harvest_state["processed"]', self.app)

    def test_cta_clears_stale_total_on_filter_change(self):
        body = _function_body(self.html, "scheduleSearch")
        self.assertIn("lastKnownTotal = null", body)
        self.assertIn("updateAnalyzeCta", body)

    def test_analyze_enqueue_still_202(self):
        self.assertIn('"/api/analyze"', self.html)
        self.assertIn("202", self.app)


if __name__ == "__main__":
    unittest.main()
