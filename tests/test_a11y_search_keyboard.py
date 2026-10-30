"""Keyboard and screen-reader checks for the search results UI in index.html."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "templates", "index.html")


def _html():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


def _fn_body(name, n=2500):
    s = _html()
    i = s.index("function %s(" % name)
    return s[i:i + n]


class SearchKeyboardA11yTest(unittest.TestCase):
    def test_logic_toggles_are_buttons_with_aria_pressed(self):
        s = _html()
        self.assertNotRegex(s, r'<span class="logic-btn')
        toggles = re.findall(r'<button type="button" class="logic-btn[^"]*" aria-pressed="(true|false)"', s)
        self.assertEqual(len(toggles), 10)
        self.assertIn("setAttribute('aria-pressed'", _fn_body("setLogic", 800))

    def test_sortable_headers_use_buttons_and_aria_sort(self):
        s = _html()
        self.assertNotRegex(s, r'<th[^>]*onclick="handleHeaderClick')
        headers = re.findall(r'<th id="header-[^"]+" class="sortable"[^>]*aria-sort="none"><button type="button" class="sort-btn" onclick="handleHeaderClick', s)
        self.assertEqual(len(headers), 23)
        self.assertIn("setAttribute('aria-sort'", _fn_body("updateSortIndicators", 1600))

    def test_result_rows_are_keyboard_operable(self):
        body = _fn_body("populateTable", 2500)
        self.assertIn("tr.tabIndex = 0", body)
        self.assertIn('ev.key === "Enter" || ev.key === " "', body)

    def test_result_count_live_region(self):
        s = _html()
        self.assertRegex(s, r'id="search-results-status" class="sr-only" role="status" aria-live="polite"')
        self.assertIn("announceSearchStatus(", _fn_body("updatePagination", 900))
        self.assertIn("announceSearchStatus(", _fn_body("populateTable", 1200))

    def test_focus_visible_styles_present(self):
        s = _html()
        self.assertIn('input[type="range"]:focus-visible', s)
        self.assertIn(".global-filters-bar .global-filter-field select:focus-visible", s)

    def test_network_graph_catch_does_not_inject_error_message(self):
        s = _html()
        self.assertNotIn("'Failed to load: ' + e.message", s)
        self.assertIn("errDiv.textContent = 'Graph failed to load. Try again.'", s)


if __name__ == "__main__":
    unittest.main()
