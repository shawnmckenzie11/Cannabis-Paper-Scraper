"""Static contracts for Mobbin Slice A feel-fast catalog UX."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from dashboard_ui_config import validate_filter_config


INDEX_PATH = Path(__file__).resolve().parent / "templates" / "index.html"
APP_PATH = Path(__file__).resolve().parent / "app.py"

HARVEST_ANALYZE_SAVE_FUNCS = (
    "saveClassificationEdit",
    "openAddPapersModal",
    "confirmPdfMatchSelection",
    "triggerPdfUpload",
    "triggerHarvest",
    "startHarvestRequest",
    "showHarvestLimitPrompt",
    "confirmHarvestLimitPrompt",
    "confirmForcedHarvest",
    "analyzeFilteredSubset",
    "startAnalyzeFilteredJob",
    "cancelAnalyzePoll",
    "exportAnalysisCSV",
    "maybeRestoreGuestAnalysisAfterLogin",
    "deleteAnalysis",
)


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


class TestSliceAFeelFastUx(unittest.TestCase):
    """Guard the five Slice A catalog UX items against regressions."""

    @classmethod
    def setUpClass(cls):
        cls.html = _read_index()

    def test_filter_agent_still_accepts_global_bar(self):
        """Chip strip must not break the §5.1 global-bar allowlist."""
        errors = validate_filter_config()
        self.assertEqual(errors, [], msg="\n".join(errors))

    def test_table_uses_stale_while_revalidate_bar(self):
        """Search refresh keeps rows visible and uses a thin Updating bar."""
        overlay_match = re.search(
            r'<div id="table-loading-overlay"[^>]*>.*?</div>',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(overlay_match, "Missing #table-loading-overlay")
        overlay = overlay_match.group(0)
        self.assertIn("table-updating-bar", overlay)
        self.assertIn("Updating", overlay)
        self.assertNotIn("inset: 0", overlay)
        self.assertNotIn("Loading papers", overlay)
        self.assertIn("wrapper.classList.toggle(\"is-updating\"", self.html)
        self.assertIn(".table-wrapper.is-updating .papers-table tbody", self.html)

    def test_sliders_commit_on_change_or_pointer_up(self):
        """Range filters must not scheduleSearch on every oninput tick."""
        range_inputs = re.findall(
            r'<input type="range"[^>]*>',
            self.html,
        )
        self.assertGreaterEqual(len(range_inputs), 20)
        for slider in range_inputs:
            self.assertNotIn("scheduleSearch()", slider)
            self.assertIn('oninput="updateSliderValues()"', slider)
            self.assertTrue(
                'onchange="commitSliderSearch()"' in slider
                or 'onpointerup="commitSliderSearch()"' in slider,
                msg=f"Slider missing commit handler: {slider}",
            )
        self.assertIn("function commitSliderSearch()", self.html)
        self.assertNotRegex(
            self.html,
            r'oninput="updateSliderValues\(\);\s*scheduleSearch\(\)"',
        )

    def test_active_filter_chips_markup_and_clear(self):
        """Chip strip lives under the global bar and supports one-click clear."""
        bar_idx = self.html.find('id="global-filters-bar"')
        chips_idx = self.html.find('id="active-filter-chips"')
        tabs_idx = self.html.find("<!-- FULL PAGE WIDTH TABS -->")
        self.assertGreater(bar_idx, 0)
        self.assertGreater(chips_idx, bar_idx)
        self.assertGreater(tabs_idx, chips_idx)
        self.assertIn('id="active-filter-chips-clear"', self.html)
        self.assertIn("function renderActiveFilterChips()", self.html)
        self.assertIn("function removeActiveFilterChip(", self.html)
        self.assertIn("function clearActiveFilterChips()", self.html)
        self.assertIn("renderActiveFilterChips()", _function_body(self.html, "triggerSearch"))

    def test_tab_rail_wires_tab_counts_api(self):
        """Tab badges consume GET /api/tab-counts rather than invented numbers."""
        app_src = APP_PATH.read_text(encoding="utf-8")
        self.assertIn('@app.route("/api/tab-counts"', app_src)
        self.assertIn("function loadTabCounts()", self.html)
        self.assertIn('fetch("/api/tab-counts")', self.html)
        for tab_key in (
            "all_original",
            "preclinical",
            "clinical",
            "review",
            "unclassified",
        ):
            self.assertIn(f'data-tab-count="{tab_key}"', self.html)
        self.assertRegex(
            self.html,
            r'document\.addEventListener\("DOMContentLoaded"[\s\S]*?loadTabCounts\(\);',
        )

    def test_harvest_analyze_save_avoid_native_dialogs(self):
        """Harvest / analyze / save flows use in-modal copy, number input, and toast."""
        self.assertIn('id="harvest-limit-prompt"', self.html)
        self.assertIn('id="harvest-limit-input"', self.html)
        self.assertIn('id="toast-stack"', self.html)
        self.assertIn('id="confirm-modal-overlay"', self.html)
        self.assertIn("function showToast(", self.html)
        self.assertIn("function showConfirmModal(", self.html)
        self.assertIn("function showHarvestLimitPrompt(", self.html)

        for name in HARVEST_ANALYZE_SAVE_FUNCS:
            body = _function_body(self.html, name)
            self.assertNotRegex(body, r"\balert\s*\(", msg=f"{name} still calls alert()")
            self.assertNotRegex(body, r"\bprompt\s*\(", msg=f"{name} still calls prompt()")
            self.assertNotRegex(body, r"(?<![.\w])confirm\s*\(", msg=f"{name} still calls confirm()")

    def test_worker_unblock_polling_survives_rebase(self):
        """#69 202 task polling must stay wired for harvest, PDF, and analyze."""
        self.assertIn("function pollBackgroundTask(", self.html)
        self.assertIn('pollBackgroundTask("/api/tasks/" + data.task_id)', self.html)
        self.assertIn('pollBackgroundTask("/api/analyze/status/" + data.task_id', self.html)
        harvest_start = _function_body(self.html, "startHarvestRequest")
        self.assertIn("Counting PubMed matches", harvest_start)
        self.assertIn("startPollingHarvestStatus()", harvest_start)
        self.assertIn('status.status === "prompt"', _function_body(self.html, "startPollingHarvestStatus"))
        self.assertIn("showHarvestLimitPrompt(", _function_body(self.html, "confirmForcedHarvest"))


if __name__ == "__main__":
    unittest.main()
