"""Spreadsheet formula-injection (OWASP CSV injection) guard shared by CSV exporters."""
import re

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")


def csv_safe_cell(val):
    """Prefix ' to strings that a spreadsheet would interpret as a formula."""
    if not isinstance(val, str):
        return val
    if val[:1] in ("\t", "\r"):
        return "'" + val
    stripped = val.lstrip()
    if stripped.startswith(CSV_FORMULA_PREFIXES):
        if _PLAIN_NUMBER.match(stripped):
            return val
        return "'" + val
    return val
