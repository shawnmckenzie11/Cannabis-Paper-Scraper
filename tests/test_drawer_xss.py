"""Drawer, calibration review and drilldown must not parse paper fields as HTML (MCK-432).

Runs the real renderDrawer / renderCalibrationDashboard / renderChartDrilldownTable /
safeHttpUrl source from index.html in node against a permissive fake DOM. Function
extraction is brace-matched, so it does not depend on indentation.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "templates", "index.html")


def extract_function(html, name):
    """Return the source of `function name(...) {...}` by matching braces outside strings/comments."""
    m = re.search(r"(?:async\s+)?function\s+%s\s*\(" % re.escape(name), html)
    if not m:
        raise AssertionError("function %s not found" % name)
    i, parens = m.end(), 1
    while parens:
        parens += {"(": 1, ")": -1}.get(html[i], 0)
        i += 1
    i = html.index("{", i)
    stack = []  # "{" code brace, "${" template expression, "`" template body
    quote = None
    while i < len(html):
        c = html[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote or c == "\n":
                quote = None
        elif stack and stack[-1] == "`":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
            elif html.startswith("${", i):
                stack.append("${")
                i += 2
                continue
        elif html.startswith("//", i) and html[i - 1] not in ":\\":
            i = html.index("\n", i)
            continue
        elif html.startswith("/*", i):
            i = html.index("*/", i) + 2
            continue
        elif c in "'\"":
            quote = c
        elif c == "`":
            stack.append("`")
        elif c == "{":
            stack.append("{")
        elif c == "}":
            top = stack.pop()
            if not stack and top == "{":
                return html[m.start():i + 1]
        i += 1
    raise AssertionError("unbalanced function %s" % name)


HARNESS = r"""
const innerHTMLWrites = [];
const all = [];
function stub() { const f = function () { return p; }; const p = new Proxy(f, { get: (t, k) => k === Symbol.toPrimitive ? (() => "") : (k === "length" ? 0 : p), apply: () => p, construct: () => p }); return p; }
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this._text = ""; this.dataset = {}; this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false }; this.value = ""; all.push(this); }
  set innerHTML(v) { if (v !== "") innerHTMLWrites.push([this.tag, String(v)]); this.children = []; this._text = ""; }
  get innerHTML() { return this.children.length || this._text ? "x" : ""; }
  set innerText(v) { this.textContent = v; }
  get innerText() { return this.textContent; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  get childNodes() { return this.children; }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this[k] = String(v); }
  getAttribute(k) { return this[k] || null; }
  addEventListener() {}
  querySelector() { return new El("div"); }
  querySelectorAll() { return []; }
  getContext() { return stub(); }
  closest() { return null; }
}
const byId = {};
const document = {
  createElement: t => new El(t),
  createTextNode: t => { const e = new El("#text"); e._text = String(t); return e; },
  getElementById: id => byId[id] || (byId[id] = new El(id)),
  querySelector: () => new El("div"), querySelectorAll: () => [],
};
const known = new Set(["document", "console", "JSON", "String", "Number", "Array", "Object", "Math", "Set", "Map", "URL", "Boolean", "encodeURI", "encodeURIComponent", "RegExp", "Date", "Error", "Promise", "undefined", "NaN", "Infinity", "isNaN", "parseInt", "parseFloat", "Symbol", "Proxy", "window", "all", "innerHTMLWrites", "errors", "byId", "El", "stub", "known", "scope"]);
const scope = new Proxy({}, { has: (t, k) => !known.has(k) && !(k in globalThis), get: (t, k) => k === Symbol.unscopables ? undefined : (k in t ? t[k] : stub()), set: (t, k, v) => { t[k] = v; return true; } });
const errors = [];
with (scope) {
  __SRC__
  for (const [fn, arg] of __CALLS__) {
    try { eval(fn)(...arg); } catch (e) { errors.push(fn + ": " + e.message); }
  }
  console.log(JSON.stringify({
    innerHTMLWrites,
    errors,
    tags: all.map(n => n.tag),
    hrefs: all.filter(n => n.tag === "a").map(n => ({ href: n.href, rel: n.rel, text: n.textContent })),
    texts: Object.fromEntries(Object.entries(byId).map(([k, v]) => [k, v.textContent])),
    safe: __URLS__.map(u => safeHttpUrl(u)),
  }));
}
"""

IMG = '<img src=x onerror="alert(1)">'
BREAK = '"><script>alert(2)</script>'
BAD_URLS = [
    "javascript:alert(1)", " JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "jav\nascript:alert(1)",
    "\x01javascript:alert(1)", "\x00https://evil.example/", "ht\ttps://evil.example/",
    "https:\\\\evil.example\\x", "https:\\/evil.example/", "data:text/html,<script>alert(3)</script>",
    "vbscript:x", "//evil.example/x", 'https://ok.example/"><img src=x onerror=alert(1)>' + "x:",
]
GOOD_URLS = ["http://ok.example/a.pdf", "https://ok.example/b.pdf"]


def _run(calls):
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    src = "\n".join(extract_function(html, n) for n in
                    ("safeHttpUrl", "renderDrawer", "renderCalibrationDashboard", "renderChartDrilldownTable"))
    js = (HARNESS.replace("__SRC__", src).replace("__CALLS__", json.dumps(calls))
          .replace("__URLS__", json.dumps(BAD_URLS + GOOD_URLS)))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def _paper(link):
    return {
        "id": 99000001, "title": IMG + "T & 'q'", "journal": IMG + "J", "year": BREAK,
        "doi": '10.1/x"><img src=x onerror=alert(1)>', "pmid": '1"><img onerror=alert(1)>',
        "full_text_link": link, "cannabis_type": [IMG + "indica"], "outcome_domain": [IMG + "sleep", BREAK],
        "study_type": ["RCT"], "exposure_method": ["oral"], "authors": ["A"],
    }


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DrawerXssTest(unittest.TestCase):
    def test_extraction_is_indentation_independent(self):
        src = "x\n  function foo(a) {\n if (a) { return '}'; } // }\n}\nfunction bar() {}"
        self.assertEqual(extract_function(src, "foo"), "function foo(a) {\n if (a) { return '}'; } // }\n}")

    def test_safe_http_url_cases(self):
        r = _run([])
        self.assertEqual(r["safe"][:len(BAD_URLS) - 1], [""] * (len(BAD_URLS) - 1))
        self.assertNotIn('"', r["safe"][len(BAD_URLS) - 1])  # breakout chars get percent-encoded
        self.assertEqual(r["safe"][len(BAD_URLS):], GOOD_URLS)

    def test_drawer_never_parses_fields_and_blocks_bad_links(self):
        calls = [["renderDrawer", [_paper(u)]] for u in BAD_URLS[:-1]]
        calls.append(["renderDrawer", [_paper("https://ok.example/full.pdf")]])
        r = _run(calls)
        self.assertEqual(r["errors"], [])
        for tag, html in r["innerHTMLWrites"]:
            self.assertNotIn("img", html)
            self.assertNotIn("script", html)
        self.assertNotIn("img", r["tags"])
        self.assertNotIn("script", r["tags"])
        hrefs = r["hrefs"]
        self.assertTrue(hrefs)
        for a in hrefs:
            self.assertRegex(a["href"], r"^https?://(doi\.org|pubmed\.ncbi\.nlm\.nih\.gov|ok\.example)/")
            self.assertNotIn('"', a["href"])
            self.assertEqual(a["rel"], "noopener noreferrer")
        self.assertIn("https://ok.example/full.pdf", [a["href"] for a in hrefs])
        self.assertEqual(sum(1 for a in hrefs if a["text"] in ("PDF", "Full text")), 1)
        self.assertIn(IMG + "J", r["texts"]["drawer-journal-year"])
        self.assertIn(IMG + "sleep", r["texts"]["drawer-outcomes-container"])

    def test_calibration_and_drilldown_escape(self):
        cal = {"priority_review": [{"paper_id": IMG, "pmid": BREAK, "variant": IMG, "title": IMG + "&'",
                                     "expert_status": BREAK, "confidence": 0.5, "in_review_queue": True,
                                     "high_level_fields": [IMG]}]}
        papers = [{"id": '1"><img onerror=alert(1)>', "title": "A & B 'c' " + IMG, "year": BREAK}]
        r = _run([["renderCalibrationDashboard", [cal]],
                  ["renderChartDrilldownTable", [papers, "study-design", "x", {"shownLimit": 10}]]])
        self.assertNotIn("img", r["tags"])
        self.assertNotIn("script", r["tags"])
        for tag, html in r["innerHTMLWrites"]:
            self.assertNotIn("img", html)
        self.assertIn(IMG + "&'", r["texts"]["cal-review-tbody"])
        self.assertIn(BREAK, r["texts"]["cal-review-tbody"])
        self.assertIn("A & B 'c' " + IMG, r["texts"]["chart-drilldown-tbody"])


if __name__ == "__main__":
    unittest.main()
