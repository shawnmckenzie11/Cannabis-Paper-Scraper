#!/usr/bin/env python3
"""Compare a pytest JUnit XML report against the known-failure baseline.

Exits 1 only when a test fails that is NOT listed in the baseline file.
Usage: check_test_baseline.py <junit.xml> <baseline.txt>
"""
import sys
import xml.etree.ElementTree as ET


def node_id(case):
    cls = case.get("classname", "")
    name = case.get("name", "")
    file = case.get("file")
    parts = cls.split(".")
    # Rebuild "path/to/test_x.py::Class::test" from the dotted classname.
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i]) + ".py"
        if file and file.replace("\\", "/") == cand or not file:
            rest = parts[i:]
            if not file:
                import os
                if not os.path.exists(cand):
                    continue
            return "::".join([cand, *rest, name])
    return f"{cls}::{name}"


def main():
    report, baseline_path = sys.argv[1], sys.argv[2]
    baseline = {l.strip() for l in open(baseline_path) if l.strip() and not l.startswith("#")}
    failed = set()
    for case in ET.parse(report).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(node_id(case))
    new = sorted(failed - baseline)
    fixed = sorted(baseline - failed)
    print(f"Failures: {len(failed)} (baseline {len(baseline)})")
    if fixed:
        print(f"\n{len(fixed)} baseline test(s) now pass; consider removing them from the baseline:")
        print("\n".join(f"  {t}" for t in fixed))
    if new:
        print(f"\n{len(new)} NEW failure(s) not in baseline:")
        print("\n".join(f"  {t}" for t in new))
        return 1
    print("\nNo new failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
