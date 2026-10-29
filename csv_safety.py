"""Spreadsheet formula-injection (OWASP CSV injection) guard shared by CSV exporters."""

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe_cell(val):
    """Prefix ' to strings that a spreadsheet would interpret as a formula."""
    if not isinstance(val, str):
        return val
    stripped = val.lstrip()
    if val.startswith(("\t", "\r")) or stripped.startswith(CSV_FORMULA_PREFIXES):
        try:
            float(val.strip())
            return val  # plain number like -0.5 or +3
        except ValueError:
            return "'" + val
    return val
