"""populateTable must build rows with textContent and only http(s) hrefs (MCK-430).

Runs the real populateTable/safeHttpUrl source from index.html in node with a
tiny fake DOM whose innerHTML setter records any HTML parsing on row content.
"""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "templates", "index.html")


def _fn_src(html, name):
    start = html.index("        function %s(" % name)
    end = html.index("\n        }\n", start) + len("\n        }\n")
    return html[start:end]


HARNESS = r"""
const innerHTMLWrites = [];
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this._text = ""; this.listeners = {}; }
  set innerHTML(v) { if (this.tag !== "tbody" || v !== "") innerHTMLWrites.push([this.tag, v]); this.children = []; }
  get innerHTML() { return ""; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(k, f) { this.listeners[k] = f; }
  all() { return [this].concat(...this.children.map(c => c.all())); }
}
const tbody = new El("tbody"), empty = new El("div"), recent = new El("select");
recent.value = "7";
const document = { createElement: t => new El(t), getElementById: id => ({"papers-table-body": tbody, "table-empty-state": empty, "filter-recent-range": recent})[id] || null };
let currentPapersInView = []; const selectedPaperIds = new Set(); const updateSelectionSummary = () => {};
const visibleColumns = new Proxy({}, { get: () => true }); const isColumnRelevant = () => true; const currentTab = "all_original";
const formatStudyDuration = d => String(d || "—"); const formatExposureMethod = m => String(m);
const openDrawer = () => {};
__SRC__
populateTable(__PAPERS__);
const nodes = tbody.all();
console.log(JSON.stringify({
  innerHTMLWrites,
  tags: nodes.map(n => n.tag),
  hrefs: nodes.filter(n => n.tag === "a").map(n => n.href),
  text: tbody.textContent,
  safe: __URLS__.map(u => safeHttpUrl(u)),
}));
"""

PAYLOAD = '<img src=x onerror="alert(1)">Evil'


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PopulateTableXssTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INDEX, encoding="utf-8") as f:
            html = f.read()
        src = _fn_src(html, "populateTable") + _fn_src(html, "safeHttpUrl")
        papers = [{
            "title": PAYLOAD, "doi": "10.1/x", "pmid": "99000001", "newly_harvested": True,
            "full_text_link": "javascript:alert(1)//x.pdf", "study_type": ['<b onmouseover=x>RCT</b>'],
            "strain_reported": '"><script>alert(2)</script>',
        }, {"title": "ok", "full_text_link": "data:text/html,<script>alert(3)</script>.pdf"},
           {"title": "pdf", "full_text_link": "https://example.org/paper.pdf"}]
        urls = ["javascript:alert(1)", " JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x",
                "//evil.example/x", "http://ok.example/a.pdf", "https://ok.example/b.pdf"]
        js = HARNESS.replace("__SRC__", src).replace("__PAPERS__", json.dumps(papers)).replace("__URLS__", json.dumps(urls))
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.result = json.loads(out.stdout)

    def test_no_html_parsing_of_paper_fields(self):
        self.assertEqual(self.result["innerHTMLWrites"], [])
        self.assertNotIn("img", self.result["tags"])
        self.assertNotIn("script", self.result["tags"])
        self.assertIn(PAYLOAD, self.result["text"])

    def test_only_http_links_rendered(self):
        hrefs = self.result["hrefs"]
        self.assertIn("https://example.org/paper.pdf", hrefs)
        self.assertFalse([h for h in hrefs if not h.startswith(("http://", "https://"))])
        self.assertEqual(len([h for h in hrefs if h.endswith(".pdf")]), 1)

    def test_safe_http_url_rejects_non_http_schemes(self):
        self.assertEqual(self.result["safe"][:5], ["", "", "", "", ""])
        self.assertEqual(self.result["safe"][5:], ["http://ok.example/a.pdf", "https://ok.example/b.pdf"])

    def test_populate_table_source_has_no_innerhtml_row_build(self):
        with open(INDEX, encoding="utf-8") as f:
            src = _fn_src(f.read(), "populateTable")
        self.assertNotIn("tr.innerHTML", src)
        self.assertNotIn("${p.", src)


if __name__ == "__main__":
    unittest.main()
