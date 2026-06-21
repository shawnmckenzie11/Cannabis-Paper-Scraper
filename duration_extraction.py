"""Exposure-method-aware duration extraction for Maude preclinical routes."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set

import extractor

INVITRO_CONDITIONED_MEDIA: Set[str] = {"smoke/vapor conditioned media"}
INVITRO_DIRECT_SMOKE: Set[str] = {"exposure of cells to smoke/vapor"}
INVITRO_DISSOLVED: Set[str] = {"cannabinoids dissolved in media"}

INVIVO_SMOKE_ROUTES: Set[str] = {"whole body. smoke/vapor", "nose only smoke/vapor"}
INVIVO_SYSTEMIC_ROUTES: Set[str] = {
    "injection cannabinoids",
    "oral administration",
    "sub-lingual",
    "intranasal",
    "intratracheal",
}

REPEAT_EXPOSURE_PATTERNS = [
    re.compile(r"(?i)\b(?:single|one)[- ]?(?:time\s+)?exposure\b"),
    re.compile(r"(?i)\b(?:two|2)\s+(?:separate\s+)?exposures?\b"),
    re.compile(r"(?i)\b(?:three|3)\s+(?:separate\s+)?exposures?\b"),
    re.compile(r"(?i)\b(\d+)\s+(?:repeat(?:ed)?\s+)?exposures?\b"),
    re.compile(r"(?i)\bexposed\s+(?:once|one\s+time)\b"),
    re.compile(r"(?i)\b(?:acute|single)\s+(?:dose|exposure|administration)\b"),
]

EXPOSURES_PER_DAY_PATTERN = re.compile(
    r"(?i)\b(\d+|once|twice|three\s+times|thrice)\s+(?:exposures?\s+)?(?:per\s+day|daily|a\s+day|each\s+day)\b"
)

ROUTE_DURATION_PATTERN = re.compile(
    r"(?i)(?:incubated|treated|exposed|cultured|maintained|received)(?:\s+\w+){0,10}\s+for\s+"
    r"(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|h(?:ou)?rs?|days?|weeks?)\b"
)

PER_EXPOSURE_DURATION_PATTERN = re.compile(
    r"(?i)(?:for|during)\s+(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|h(?:ou)?rs?)\b(?:\s+(?:of\s+)?(?:exposure|smoke|vapor|inhalation))?"
)


def _format_duration_value(value: str, unit_raw: str) -> str:
    """Formats a numeric duration token and unit into a canonical label."""
    unit = unit_raw.lower().rstrip(".")
    val_f = float(value)
    if val_f.is_integer():
        val_f = int(val_f)
    if unit.startswith("min"):
        unit_label = "minute" if val_f == 1 else "minutes"
    elif unit.startswith("h"):
        unit_label = "hour" if val_f == 1 else "hours"
    elif unit.startswith("week"):
        unit_label = "week" if val_f == 1 else "weeks"
    else:
        unit_label = "day" if val_f == 1 else "days"
    return f"{val_f} {unit_label}"


def extract_route_duration_label(text: str) -> Optional[str]:
    """Extracts a free-text treatment/exposure duration label from Methods phrasing."""
    if not text:
        return None
    labeled = extractor.extract_treatment_duration(text)
    if labeled:
        return labeled
    match = ROUTE_DURATION_PATTERN.search(text)
    if match:
        return _format_duration_value(match.group(1), match.group(2))
    match = PER_EXPOSURE_DURATION_PATTERN.search(text)
    if match:
        return _format_duration_value(match.group(1), match.group(2))
    return None


def extract_per_exposure_duration(text: str) -> Optional[str]:
    """Extracts minutes/hours per discrete smoke or vapor exposure."""
    if not text:
        return None
    labeled = extractor.extract_inhaled_exposure_duration(text)
    if labeled:
        return labeled
    match = PER_EXPOSURE_DURATION_PATTERN.search(text)
    if match:
        return _format_duration_value(match.group(1), match.group(2))
    return None


def _normalize_labels(values: Any) -> List[str]:
    """Returns a list of non-empty string labels from study_type or exposure_method."""
    if values is None:
        return []
    if isinstance(values, str):
        stripped = values.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                import json

                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                pass
        return [stripped] if stripped else []
    if isinstance(values, list):
        return [str(item).strip() for item in values if str(item).strip()]
    return []


def extract_repeat_exposure_count(text: str) -> Optional[int]:
    """Extracts the number of discrete repeat exposures from free text."""
    if not text:
        return None
    for pattern in REPEAT_EXPOSURE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if match.lastindex:
            return int(match.group(1))
        lowered = match.group(0).lower()
        if "two" in lowered or "2 " in lowered:
            return 2
        if "three" in lowered or "3 " in lowered:
            return 3
        return 1
    return None


def extract_exposures_per_day(text: str) -> Optional[str]:
    """Extracts exposures-per-day phrasing for in vivo smoke/systemic routes."""
    if not text:
        return None
    match = EXPOSURES_PER_DAY_PATTERN.search(text)
    if not match:
        return extractor.extract_administration_frequency(text)
    token = match.group(1).lower()
    if token == "once":
        return "1 exposure/day"
    if token == "twice":
        return "2 exposures/day"
    if token == "three times" or token == "thrice":
        return "3 exposures/day"
    if token.isdigit():
        count = int(token)
        label = "exposure" if count == 1 else "exposures"
        return f"{count} {label}/day"
    return match.group(0).strip()


def infer_exposure_regimen_bin(
    duration_days: Optional[float],
    repeat_exposure_count: Optional[int],
) -> Optional[str]:
    """Bins in vivo smoke exposure length into acute, subchronic, or chronic."""
    if repeat_exposure_count is not None and 1 <= repeat_exposure_count <= 2:
        return "acute"
    if duration_days is not None:
        if duration_days > 14:
            return "chronic"
        if duration_days >= 4:
            return "subchronic"
    if repeat_exposure_count is not None and repeat_exposure_count <= 2:
        return "acute"
    return None


def _empty_duration_profile() -> Dict[str, Any]:
    """Returns a blank duration profile payload."""
    return {
        "duration_days": None,
        "inhaled_exposure_duration": None,
        "administration_frequency": None,
        "treatment_duration": None,
        "exposure_regimen_bin": None,
        "repeat_exposure_count": None,
    }


def _extract_invitro_duration(text: str, exposure_list: Sequence[str]) -> Dict[str, Any]:
    """Extracts in vitro duration fields by exposure route."""
    profile = _empty_duration_profile()
    exposure_set = set(exposure_list)

    if exposure_set & INVITRO_DIRECT_SMOKE:
        profile["inhaled_exposure_duration"] = extract_per_exposure_duration(text)
        profile["repeat_exposure_count"] = extract_repeat_exposure_count(text)
        return profile

    if exposure_set & INVITRO_CONDITIONED_MEDIA or exposure_set & INVITRO_DISSOLVED:
        profile["treatment_duration"] = extract_route_duration_label(text)
        return profile

    profile["treatment_duration"] = extract_route_duration_label(text)
    return profile


def _extract_invivo_smoke_duration(text: str) -> Dict[str, Any]:
    """Extracts in vivo whole-body or nose-only smoke/vape duration fields."""
    profile = _empty_duration_profile()
    profile["inhaled_exposure_duration"] = extract_per_exposure_duration(text)
    profile["administration_frequency"] = extract_exposures_per_day(text)
    profile["duration_days"] = extractor.extract_duration_days(text)
    profile["repeat_exposure_count"] = extract_repeat_exposure_count(text)
    profile["exposure_regimen_bin"] = infer_exposure_regimen_bin(
        profile["duration_days"],
        profile["repeat_exposure_count"],
    )
    return profile


def _extract_invivo_systemic_duration(text: str) -> Dict[str, Any]:
    """Extracts in vivo injection/oral/local administration duration fields."""
    profile = _empty_duration_profile()
    profile["administration_frequency"] = extract_exposures_per_day(text)
    profile["duration_days"] = extractor.extract_duration_days(text)
    return profile


def extract_exposure_duration_profile(
    title: str,
    abstract: str,
    study_type: Any,
    exposure_method: Any,
) -> Dict[str, Any]:
    """Extracts duration fields aligned with the preclinical exposure-route appendix."""
    text = f"{title} {abstract or ''}"
    study_labels = _normalize_labels(study_type)
    exposure_list = _normalize_labels(exposure_method)
    exposure_set = set(exposure_list)

    is_clinical = any(label.startswith("Clinical (") for label in study_labels)
    is_invivo = any(label.startswith("Animal Models (") for label in study_labels)
    is_invitro = any(label.startswith("Cell Culture (") for label in study_labels)

    if is_invitro:
        return _extract_invitro_duration(text, exposure_list)

    if is_invivo:
        if exposure_set & INVIVO_SMOKE_ROUTES:
            return _extract_invivo_smoke_duration(text)
        if exposure_set & INVIVO_SYSTEMIC_ROUTES:
            return _extract_invivo_systemic_duration(text)
        profile = _extract_invivo_systemic_duration(text)
        if profile["duration_days"] is None:
            profile["duration_days"] = extractor.extract_duration_days(text)
        return profile

    if is_clinical:
        profile = _empty_duration_profile()
        profile["duration_days"] = extractor.extract_duration_days(text)
        is_inhaled = any(
            token in method.lower()
            for method in exposure_list
            for token in ("inhaled", "smok", "vapor", "nose", "whole body")
        )
        if is_inhaled:
            profile["inhaled_exposure_duration"] = extractor.extract_inhaled_exposure_duration(text)
        profile["administration_frequency"] = extractor.extract_administration_frequency(text)
        return profile

    return _empty_duration_profile()
