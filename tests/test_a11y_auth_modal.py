"""Accessibility checks for the auth modal, error escaping, and avatar label in index.html."""
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


class AuthModalFocusTrapTest(unittest.TestCase):
    def test_initial_focus_and_opener_saved(self):
        body = _fn_body("showAuthModal", 600)
        self.assertIn("authModalReturnFocus = document.activeElement", body)
        self.assertIn(".focus()", body)

    def test_close_restores_focus(self):
        body = _fn_body("closeAuthModal", 600)
        self.assertIn("authModalReturnFocus.focus()", body)

    def test_escape_and_tab_cycle(self):
        s = _html()
        i = s.index("function closeAuthModal(")
        chunk = s[i:i + 3000]
        self.assertIn('e.key === "Escape"', chunk)
        self.assertIn('e.key === "Tab"', chunk)
        self.assertIn("e.shiftKey && document.activeElement === first", chunk)
        self.assertIn("document.activeElement === last", chunk)
        self.assertIn("last.focus()", chunk)
        self.assertIn("first.focus()", chunk)


class ErrorEscapingTest(unittest.TestCase):
    def test_no_innerhtml_concat_of_data_error(self):
        self.assertIsNone(re.search(r"innerHTML\s*=[^;\n]*\+\s*data\.error", _html()))

    def test_graph_error_uses_textcontent(self):
        self.assertIn("errDiv.textContent = 'Error: ' + String(data.error)", _html())


class AvatarLabelTest(unittest.TestCase):
    def test_avatar_has_aria_label(self):
        m = re.search(r'<div[^>]*id="user-avatar-btn"[^>]*>', _html())
        self.assertTrue(m)
        self.assertIn('aria-label="Account menu"', m.group(0))
        self.assertIn('role="button"', m.group(0))


if __name__ == "__main__":
    unittest.main()
