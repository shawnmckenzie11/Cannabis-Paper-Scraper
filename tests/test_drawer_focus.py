"""Details drawer focus management (MCK-432): focus moves into the drawer on open,
Escape closes it, and focus returns to the opener on close.

Runs the real openDrawer / renderDrawer / closeDrawer / handleDrawerEscape source from
index.html in node against a fake DOM that tracks classList and focus.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

from tests.test_drawer_xss import INDEX, extract_function

HARNESS = r"""
let active = null;
function stub() { const f = function () { return p; }; const p = new Proxy(f, { get: (t, k) => k === Symbol.toPrimitive ? (() => "") : (k === "length" ? 0 : p), apply: () => p, construct: () => p }); return p; }
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this._text = ""; this.dataset = {}; this.value = "";
    const cls = new Set(); this.classList = { add: c => cls.add(c), remove: c => cls.delete(c), toggle: c => cls.has(c) ? cls.delete(c) : cls.add(c), contains: c => cls.has(c) }; }
  set innerHTML(v) { this.children = []; this._text = ""; }
  get innerHTML() { return ""; }
  set innerText(v) { this.textContent = v; }
  get innerText() { return this.textContent; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
  get childNodes() { return this.children; }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this[k] = String(v); }
  getAttribute(k) { return this[k] || null; }
  addEventListener() {}
  querySelector() { return new El("div"); }
  querySelectorAll() { return []; }
  getContext() { return stub(); }
  closest() { return null; }
  focus() { active = this; }
}
const byId = {};
const document = {
  get activeElement() { return active; },
  createElement: t => new El(t),
  createTextNode: t => { const e = new El("#text"); e._text = String(t); return e; },
  getElementById: id => byId[id] || (byId[id] = new El(id)),
  querySelector: () => new El("div"), querySelectorAll: () => [],
  addEventListener() {},
};
const known = new Set(["document", "console", "JSON", "String", "Number", "Array", "Object", "Math", "Set", "Map", "URL", "Boolean", "encodeURI", "encodeURIComponent", "RegExp", "Date", "Error", "Promise", "undefined", "NaN", "Infinity", "isNaN", "parseInt", "parseFloat", "Symbol", "Proxy", "window", "byId", "El", "stub", "known", "scope", "active", "out", "key"]);
const scope = new Proxy({}, { has: (t, k) => !known.has(k) && !(k in globalThis), get: (t, k) => k === Symbol.unscopables ? undefined : (k in t ? t[k] : stub()), set: (t, k, v) => { t[k] = v; return true; } });
const out = {};
with (scope) {
  __SRC__
  const paper = { id: 1, title: "T", abstract: "A", authors: ["X"], journal: "J", year: 2020 };
  const opener = document.createElement("button"); opener.focus();
  const drawer = document.getElementById("details-drawer");
  openDrawer(paper);
  out.openAfterOpen = drawer.classList.contains("open");
  out.focusAfterOpen = active && active.tag;
  const key = (k, target) => { const e = { key: k, target: target || active, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; handleDrawerEscape(e); return e; };
  const input = document.createElement("input"); input.tagName = "INPUT";
  key("Escape", input);
  out.openAfterEscInInput = drawer.classList.contains("open");
  key("Enter");
  out.openAfterEnter = drawer.classList.contains("open");
  const esc = key("Escape");
  out.openAfterEscape = drawer.classList.contains("open");
  out.escPrevented = esc.defaultPrevented;
  out.focusReturned = active === opener;
  openDrawer(paper);
  document.getElementById("auth-required-modal").style.display = "flex";
  key("Escape");
  out.openWithAuthModal = drawer.classList.contains("open");
  document.getElementById("auth-required-modal").style.display = "none";
  active = document.getElementById("btn-close-drawer");
  closeDrawer();
  out.focusReturnedViaClose = active === opener;
  console.log(JSON.stringify(out));
}
"""


def _html():
    with open(INDEX, encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DrawerFocusRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = _html()
        src = "\n".join(extract_function(html, n) for n in
                        ("safeHttpUrl", "openDrawer", "renderDrawer", "closeDrawer", "handleDrawerEscape"))
        src = "let drawerReturnFocus = null;\n" + src
        res = subprocess.run(["node", "-e", HARNESS.replace("__SRC__", src)],
                             capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_open_moves_focus_to_close_button(self):
        self.assertTrue(self.out["openAfterOpen"])
        self.assertEqual(self.out["focusAfterOpen"], "btn-close-drawer")

    def test_escape_closes_and_returns_focus(self):
        self.assertFalse(self.out["openAfterEscape"])
        self.assertTrue(self.out["escPrevented"])
        self.assertTrue(self.out["focusReturned"])

    def test_escape_ignored_in_inputs_other_keys_and_under_auth_modal(self):
        self.assertTrue(self.out["openAfterEscInInput"])
        self.assertTrue(self.out["openAfterEnter"])
        self.assertTrue(self.out["openWithAuthModal"])

    def test_close_button_returns_focus(self):
        self.assertTrue(self.out["focusReturnedViaClose"])


class DrawerFocusSourceTest(unittest.TestCase):
    def test_drawer_dialog_semantics_and_close_button(self):
        s = _html()
        aside = re.search(r'<aside[^>]*id="details-drawer"[^>]*>', s).group(0)
        self.assertIn('role="dialog"', aside)
        self.assertIn('aria-labelledby="drawer-title"', aside)
        btn = re.search(r'<button[^>]*id="btn-close-drawer"[^>]*>', s).group(0)
        self.assertIn('onclick="closeDrawer()"', btn)
        self.assertIn("aria-label=", btn)

    def test_escape_listener_registered(self):
        self.assertIn('document.addEventListener("keydown", handleDrawerEscape)', _html())


if __name__ == "__main__":
    unittest.main()
