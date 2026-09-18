"""Static contracts for Mobbin Slice Analyze-1 (A1–A4) with Wonder copy."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent / "templates" / "index.html"
APP_PATH = Path(__file__).resolve().parent / "app.py"

WONDER_CONFIRM = "Analyze "
WONDER_CONFIRM_MATCHING = " articles matching "
WONDER_WAIT = "Analyzing… · you can leave this page"
WONDER_CANCEL = "Cancel analysis"
WONDER_EMPTY = "Nothing to analyze with these filters."
WONDER_ERROR = "Analysis didn’t finish. Try again."
WONDER_RETRY = "Retry"
WONDER_DONE = "Analysis ready."
WONDER_OPEN = "Open results"
WONDER_GUEST = "Sign in to save analyses."


def _read_index() -> str:
    """Return the catalog SPA source."""
    return INDEX_PATH.read_text(encoding="utf-8")


def _function_body(html: str, name: str) -> str:
    """Return the source of a top-level function in index.html."""
    match = re.search(rf"function {re.escape(name)}\s*\(", html)
    if not match:
        raise AssertionError(f"function {name}() not found in templates/index.html")
    start = match.start()
    depth = 0
    close_paren = None
    for idx in range(match.end() - 1, len(html)):
        char = html[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close_paren = idx
                break
    if close_paren is None:
        raise AssertionError(f"Could not parse parameter list for {name}()")
    brace = html.find("{", close_paren)
    if brace < 0:
        raise AssertionError(f"Could not find body for {name}()")
    depth = 0
    for idx in range(brace, len(html)):
        char = html[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[start : idx + 1]
    raise AssertionError(f"Could not parse function body for {name}()")


class TestAnalyzeSlice1(unittest.TestCase):
    """Guard Analyze-1 scope chrome, wait UX, empty gate, and first-paint path."""

    @classmethod
    def setUpClass(cls):
        cls.html = _read_index()
        cls.app_src = APP_PATH.read_text(encoding="utf-8")

    def test_wonder_copy_is_exact(self):
        """Wonder stamped strings must appear verbatim — no alternate wording."""
        self.assertIn(WONDER_WAIT, self.html)
        self.assertIn(WONDER_CANCEL, self.html)
        self.assertIn(WONDER_EMPTY, self.html)
        self.assertIn(WONDER_ERROR, self.html)
        self.assertIn(WONDER_RETRY, self.html)
        self.assertIn(WONDER_DONE, self.html)
        self.assertIn(WONDER_OPEN, self.html)
        self.assertIn(WONDER_GUEST, self.html)
        confirm = _function_body(self.html, "formatAnalyzeConfirmMessage")
        self.assertIn(WONDER_CONFIRM_MATCHING, confirm)
        self.assertIn('return "Analyze " + n + " articles matching "', confirm)
        self.assertNotIn("Log in to save and view analysis results", self.html)
        self.assertNotIn("Analysis running…", self.html)
        self.assertNotIn("No papers matched — adjust filters.", self.html)
        self.assertNotIn("Analysis failed. Try again.", self.html)

    def test_cta_shows_count_and_filter_summary(self):
        """A1: CTA label uses lastKnownTotal; summary mirrors active chips."""
        self.assertIn('id="btn-analyze-subset"', self.html)
        self.assertIn('id="analyze-cta-label"', self.html)
        self.assertIn('id="analyze-filter-summary"', self.html)
        self.assertIn("Analyze — articles", self.html)
        self.assertIn("function updateAnalyzeCta()", self.html)
        self.assertIn("function formatAnalyzeCountLabel(", self.html)
        self.assertIn("function formatAnalyzeFilterSummary()", self.html)
        self.assertIn("lastKnownTotal", _function_body(self.html, "updateAnalyzeCta"))
        self.assertIn("collectActiveFilterChips()", _function_body(self.html, "formatAnalyzeFilterSummary"))
        self.assertIn("updateAnalyzeCta()", _function_body(self.html, "renderActiveFilterChips"))
        self.assertIn("updateAnalyzeCta()", _function_body(self.html, "updatePagination"))

    def test_cta_disabled_when_count_is_zero(self):
        """A1/A3: do not fire Analyze when the catalog match count is 0."""
        cta = _function_body(self.html, "setAnalyzeChrome")
        self.assertIn("lastKnownTotal === 0", cta)
        self.assertIn("btn.disabled = busy || empty", cta)
        gate = _function_body(self.html, "analyzeFilteredSubset")
        self.assertIn("lastKnownTotal === 0", gate)
        self.assertIn('setAnalyzeChrome("empty")', gate)
        self.assertNotIn("fetch(\"/api/analyze\"", gate)
        self.assertNotIn("showToast(", gate)

    def test_calm_confirm_before_enqueue(self):
        """A1: Wonder confirm copy via in-app modal, no native dialogs."""
        body = _function_body(self.html, "analyzeFilteredSubset")
        self.assertIn("showConfirmModal(", body)
        self.assertIn("formatAnalyzeConfirmMessage()", body)
        self.assertIn('"Analyze"', body)
        self.assertIn("startAnalyzeFilteredJob()", body)
        self.assertNotRegex(body, r"\balert\s*\(")
        self.assertNotRegex(body, r"\bprompt\s*\(")
        self.assertNotRegex(body, r"(?<![.\w])confirm\s*\(")

    def test_waiting_chrome_is_persistent_not_toast(self):
        """A2: wait copy + Cancel analysis; no per-tick toasts."""
        self.assertIn('id="btn-analyze-cancel"', self.html)
        self.assertIn(">Cancel analysis<", self.html)
        self.assertIn("function cancelAnalyzePoll()", self.html)
        self.assertIn("function setAnalyzeChrome(", self.html)
        start = _function_body(self.html, "startAnalyzeFilteredJob")
        self.assertIn('setAnalyzeChrome("waiting")', start)
        self.assertNotIn("showToast(", start)
        chrome = _function_body(self.html, "setAnalyzeChrome")
        self.assertIn(WONDER_WAIT, chrome)
        self.assertIn("aria-busy", chrome)
        self.assertIn("analyzePollCancelled", _function_body(self.html, "cancelAnalyzePoll"))
        self.assertIn("isCancelled", start)

    def test_empty_server_result_shows_dedicated_state(self):
        """A3: zero-article job result uses Wonder empty copy and does not paint charts."""
        start = _function_body(self.html, "startAnalyzeFilteredJob")
        self.assertIn('setAnalyzeChrome("empty")', start)
        self.assertNotIn("paintAnalysisFromJobResult(", start)
        self.assertNotIn("openMyAnalyses(", start)
        chrome = _function_body(self.html, "setAnalyzeChrome")
        self.assertIn(WONDER_EMPTY, chrome)
        empty = _function_body(self.html, "showAnalysisEmptyState")
        self.assertIn(WONDER_EMPTY, empty)

    def test_error_and_done_chrome(self):
        """Error offers Retry; done offers Open results without a toast."""
        start = _function_body(self.html, "startAnalyzeFilteredJob")
        self.assertIn('setAnalyzeChrome("error")', start)
        self.assertIn('setAnalyzeChrome("ready")', start)
        self.assertIn("pendingAnalyzeResult = result", start)
        chrome = _function_body(self.html, "setAnalyzeChrome")
        self.assertIn(WONDER_ERROR, chrome)
        self.assertIn(WONDER_DONE, chrome)
        self.assertIn('id="btn-analyze-retry"', self.html)
        self.assertIn(">Retry<", self.html)
        self.assertIn('id="btn-analyze-open-results"', self.html)
        self.assertIn(">Open results<", self.html)
        self.assertIn("function retryAnalyzeFilteredJob()", self.html)
        self.assertIn("function openAnalyzeResults()", self.html)

    def test_first_paint_uses_job_result_not_load_analysis(self):
        """A4: Open results paints task.result; no GET /analyses/:id before first paint."""
        start = _function_body(self.html, "startAnalyzeFilteredJob")
        paint = _function_body(self.html, "paintAnalysisFromJobResult")
        open_results = _function_body(self.html, "openAnalyzeResults")
        self.assertIn("paintAnalysisFromJobResult(pendingAnalyzeResult)", open_results)
        self.assertNotIn("loadAnalysis(", start)
        self.assertNotIn("loadAnalysis(", paint)
        self.assertNotIn("loadAnalysis(", open_results)
        self.assertIn("renderChartsFromData(result.chart_data)", paint)
        self.assertIn("loadAnalysesList(result.id)", paint)
        self.assertIn("showGuestAnalysisPreview(result)", paint)
        self.assertIn("function ensureAnalysisPapersForDrilldown()", self.html)

    def test_worker_unblock_contract_unchanged(self):
        """#69: analyze stays 202 + status poll with caps."""
        self.assertIn('@app.route("/api/analyze"', self.app_src)
        self.assertIn("return jsonify({", self.app_src)
        self.assertIn('"status": "pending"', self.app_src)
        self.assertIn('pollBackgroundTask("/api/analyze/status/" + data.task_id', self.html)
        start = _function_body(self.html, "startAnalyzeFilteredJob")
        self.assertIn('fetch("/api/analyze"', start)
        self.assertIn("status === 202 && data.task_id", start)
        self.assertIn("ANALYZE_PAPER_CAP", self.app_src)
        self.assertIn("ANALYZE_DRILLDOWN_CAP", self.app_src)
        self.assertIn("cap_message", self.app_src)

    def test_no_native_dialogs_on_analyze_path(self):
        """#70: analyze wait/fail/cancel stay on toast + in-app confirm."""
        for name in (
            "analyzeFilteredSubset",
            "startAnalyzeFilteredJob",
            "cancelAnalyzePoll",
            "paintAnalysisFromJobResult",
            "openAnalyzeResults",
            "retryAnalyzeFilteredJob",
            "setAnalyzeChrome",
        ):
            body = _function_body(self.html, name)
            self.assertNotRegex(body, r"\balert\s*\(", msg=f"{name} still calls alert()")
            self.assertNotRegex(body, r"\bprompt\s*\(", msg=f"{name} still calls prompt()")
            self.assertNotRegex(body, r"(?<![.\w])confirm\s*\(", msg=f"{name} still calls confirm()")


if __name__ == "__main__":
    unittest.main()
