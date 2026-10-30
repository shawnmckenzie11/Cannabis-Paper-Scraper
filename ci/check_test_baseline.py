#!/usr/bin/env python3
"""Compare a pytest JUnit XML report against the known-failure baseline.

Fails (exit 1) when:
  * a test fails that is NOT in the baseline,
  * a baseline test did not run at all (deleted/renamed; update the baseline),
  * fewer than --min-total tests were collected,
  * --strict is given and a baseline test now passes.
Warns (no failure) when a baseline test passed or was skipped.

Usage: check_test_baseline.py <junit.xml> <baseline.txt> [--min-total N] [--strict]
"""
import argparse
import os
import sys
import xml.etree.ElementTree as ET


def node_id(case):
    """Rebuild 'path/test_x.py::Class::test' from JUnit classname + name."""
    parts = case.get("classname", "").split(".")
    name = case.get("name", "")
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i]) + ".py"
        if os.path.exists(cand):
            return "::".join([cand, *parts[i:], name])
    return f"{case.get('classname', '')}::{name}"


def load_baseline(path):
    ids = set()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                ids.add(line)
    return ids


def show(title, items):
    if items:
        print(f"\n{title} ({len(items)}):")
        print("\n".join(f"  {t}" for t in sorted(items)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("baseline")
    ap.add_argument("--min-total", type=int, default=0)
    ap.add_argument("--strict", action="store_true", help="fail when a baseline test now passes")
    args = ap.parse_args()

    baseline = load_baseline(args.baseline)
    failed, passed, skipped = set(), set(), set()
    for case in ET.parse(args.report).getroot().iter("testcase"):
        nid = node_id(case)
        if (case.find("failure") is not None) or (case.find("error") is not None):
            failed.add(nid)
        elif case.find("skipped") is not None:
            skipped.add(nid)
        else:
            passed.add(nid)
    total = len(failed) + len(passed) + len(skipped)
    ran = failed | passed | skipped

    new = failed - baseline
    now_pass = baseline & passed
    now_skip = baseline & skipped
    missing = baseline - ran

    print(f"Total {total}: {len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped "
          f"(baseline {len(baseline)})")
    show("WARNING: baseline tests that now PASS (remove from baseline)", now_pass)
    show("WARNING: baseline tests that were SKIPPED", now_skip)
    show("ERROR: baseline tests that DID NOT RUN (deleted/renamed? update baseline)", missing)
    show("ERROR: NEW failures not in baseline", new)

    bad = bool(new or missing)
    if total == 0 or total < args.min_total:
        print(f"\nERROR: only {total} tests ran (minimum {args.min_total}).")
        bad = True
    if args.strict and now_pass:
        print("\nERROR: --strict and baseline tests now pass.")
        bad = True
    print("\nFAIL" if bad else "\nOK: no new failures.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
