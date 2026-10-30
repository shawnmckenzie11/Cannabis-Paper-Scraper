"""Cannabinoid drawer block and active-filter chips must not parse values as HTML/JS.

Source-level regression tests in the style of tests/test_drawer_xss.py: the
functions are brace-extracted from templates/index.html.
"""
import os
import re
import unittest

from tests.test_drawer_xss import INDEX, extract_function


def _html():
    with open(INDEX, encoding="utf-8") as fh:
        return fh.read()


class CannabinoidProfileXssTest(unittest.TestCase):
    def setUp(self):
        self.src = extract_function(_html(), "renderCannabinoidProfile")

    def test_no_innerhtml_append(self):
        self.assertNotIn("innerHTML +=", self.src)
        self.assertIsNone(re.search(r"innerHTML\s*=\s*`", self.src))

    def test_builds_with_textcontent(self):
        self.assertIn("document.createElement", self.src)
        self.assertIn("labelEl.textContent = def.label", self.src)
        self.assertIn("valueEl.textContent = def.format(val)", self.src)


class ActiveFilterChipXssTest(unittest.TestCase):
    def setUp(self):
        self.src = extract_function(_html(), "renderActiveFilterChips")

    def test_no_inline_onclick(self):
        self.assertNotIn("onclick=", self.src)
        self.assertNotIn("decodeURIComponent('", self.src)

    def test_uses_data_attributes_and_listener(self):
        self.assertIn('data-chip-key="${key}"', self.src)
        self.assertIn('data-chip-value="${value}"', self.src)
        self.assertIn('addEventListener("click"', self.src)
        self.assertIn("btn.dataset.chipKey", self.src)


if __name__ == "__main__":
    unittest.main()
