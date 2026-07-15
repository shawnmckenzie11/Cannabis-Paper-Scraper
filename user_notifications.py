"""User notification preferences and digest email delivery."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import email_service

logger = logging.getLogger(__name__)

VALID_FREQUENCIES = ("daily", "weekly", "monthly")
FREQUENCY_TO_RECENT_RANGE = {
    "daily": "today",
    "weekly": "week",
    "monthly": "month",
}
FREQUENCY_MIN_INTERVAL = {
    "daily": timedelta(hours=20),
    "weekly": timedelta(days=6),
    "monthly": timedelta(days=27),
}
MAX_KEYWORD_ALERTS = 3
CATALOG_URL = "https://cannabis-paper-scraper.fly.dev/"


def default_notification_preferences() -> Dict[str, Any]:
    """Return empty/default notification preference structure."""
    return {
        "frequency": "weekly",
        "keyword_alerts": [{"keyword": "", "enabled": False} for _ in range(MAX_KEYWORD_ALERTS)],
        "summaries_enabled": False,
        "last_digest_sent_at": None,
    }


def normalize_notification_preferences(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Sanitize user notification preferences from form/storage."""
    base = default_notification_preferences()
    data = raw if isinstance(raw, dict) else {}

    frequency = str(data.get("frequency") or base["frequency"]).strip().lower()
    if frequency not in VALID_FREQUENCIES:
        frequency = base["frequency"]
    base["frequency"] = frequency

    alerts_in = data.get("keyword_alerts") or []
    alerts: List[Dict[str, Any]] = []
    if isinstance(alerts_in, list):
        for item in alerts_in[:MAX_KEYWORD_ALERTS]:
            if not isinstance(item, dict):
                continue
            keyword = str(item.get("keyword") or "").strip()[:120]
            enabled = bool(item.get("enabled")) and bool(keyword)
            alerts.append({"keyword": keyword, "enabled": enabled})
    while len(alerts) < MAX_KEYWORD_ALERTS:
        alerts.append({"keyword": "", "enabled": False})
    base["keyword_alerts"] = alerts[:MAX_KEYWORD_ALERTS]

    base["summaries_enabled"] = bool(data.get("summaries_enabled"))
    last_sent = data.get("last_digest_sent_at")
    base["last_digest_sent_at"] = str(last_sent) if last_sent else None
    return base


def preferences_from_form(form) -> Dict[str, Any]:
    """Build notification preferences from a Flask request form."""
    frequency = (form.get("frequency") or "weekly").strip().lower()
    alerts = []
    for idx in range(MAX_KEYWORD_ALERTS):
        keyword = (form.get(f"alert_keyword_{idx}") or "").strip()
        enabled = form.get(f"alert_enabled_{idx}") in {"1", "on", "true", "yes"}
        alerts.append({"keyword": keyword, "enabled": enabled and bool(keyword)})
    return normalize_notification_preferences(
        {
            "frequency": frequency,
            "keyword_alerts": alerts,
            "summaries_enabled": form.get("summaries_enabled") in {"1", "on", "true", "yes"},
            "last_digest_sent_at": form.get("last_digest_sent_at") or None,
        }
    )


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def digest_is_due(prefs: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """Return True when a digest should be considered for this preference set."""
    now = now or datetime.now()
    prefs = normalize_notification_preferences(prefs)
    has_alerts = any(a.get("enabled") and a.get("keyword") for a in prefs["keyword_alerts"])
    if not has_alerts and not prefs.get("summaries_enabled"):
        return False
    last = _parse_ts(prefs.get("last_digest_sent_at"))
    if last is None:
        return True
    interval = FREQUENCY_MIN_INTERVAL.get(prefs["frequency"], FREQUENCY_MIN_INTERVAL["weekly"])
    return (now - last) >= interval


def _top_counts(mapping: Dict[str, Any], *, limit: int = 8) -> List[Tuple[str, int]]:
    items = []
    for key, value in (mapping or {}).items():
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        items.append((str(key), count))
    items.sort(key=lambda pair: (-pair[1], pair[0]))
    return items[:limit]


def _format_count_table(title: str, mapping: Dict[str, Any]) -> str:
    rows = _top_counts(mapping)
    if not rows:
        return ""
    lines = [f"<h3 style='color:#f8fafc;font-size:15px;margin:18px 0 8px;'>{title}</h3>"]
    lines.append("<table style='width:100%;border-collapse:collapse;font-size:13px;'>")
    for label, count in rows:
        lines.append(
            "<tr>"
            f"<td style='padding:4px 0;color:#94a3b8;border-bottom:1px solid rgba(255,255,255,0.06);'>{label}</td>"
            f"<td style='padding:4px 0;text-align:right;color:#f8fafc;border-bottom:1px solid rgba(255,255,255,0.06);'>{count}</td>"
            "</tr>"
        )
    lines.append("</table>")
    return "".join(lines)


def build_summary_sections(chart_data: Dict[str, Any], papers: List[Dict[str, Any]], *, timeframe_label: str) -> Tuple[str, str]:
    """Build text/html summary sections from analysis chart aggregates."""
    paper_count = int((chart_data or {}).get("paper_count") or len(papers) or 0)
    text_lines = [
        f"Catalog summary ({timeframe_label})",
        f"Papers added: {paper_count}",
        "",
    ]
    html_parts = [
        f"<h2 style='color:#6366f1;font-size:18px;margin:0 0 8px;'>Catalog summary</h2>",
        f"<p style='color:#94a3b8;margin:0 0 12px;'>Papers harvested in this {timeframe_label}: "
        f"<strong style='color:#f8fafc;'>{paper_count}</strong></p>",
    ]

    sections = [
        ("Study design", (chart_data or {}).get("study_design") or {}),
        ("Cannabis type", (chart_data or {}).get("cannabis_type") or {}),
        ("Outcome domains", (chart_data or {}).get("outcome") or {}),
        ("Clinical exposure", (chart_data or {}).get("clinical_exposure") or {}),
        ("In vitro exposure", (chart_data or {}).get("vitro_exposure") or {}),
        ("In vivo exposure", (chart_data or {}).get("vivo_exposure") or {}),
    ]
    for title, mapping in sections:
        rows = _top_counts(mapping)
        if not rows:
            continue
        text_lines.append(title)
        for label, count in rows:
            text_lines.append(f"  - {label}: {count}")
        text_lines.append("")
        html_parts.append(_format_count_table(title, mapping))

    if papers:
        text_lines.append("Sample titles")
        html_parts.append("<h3 style='color:#f8fafc;font-size:15px;margin:18px 0 8px;'>Sample titles</h3><ul style='padding-left:18px;color:#94a3b8;'>")
        for paper in papers[:8]:
            title = (paper.get("title") or "Untitled").strip()
            year = paper.get("year") or ""
            text_lines.append(f"  - {title}" + (f" ({year})" if year else ""))
            html_parts.append(f"<li style='margin-bottom:6px;'>{title}" + (f" <span style='color:#64748b;'>({year})</span>" if year else "") + "</li>")
        html_parts.append("</ul>")
        text_lines.append("")

    return "\n".join(text_lines), "".join(html_parts)


def build_keyword_alert_sections(
    alert_results: List[Dict[str, Any]],
    *,
    timeframe_label: str,
) -> Tuple[str, str]:
    """Build text/html sections for keyword paper alerts."""
    if not alert_results:
        return "", ""
    text_parts = [f"New paper alerts ({timeframe_label})", ""]
    html_parts = [
        f"<h2 style='color:#6366f1;font-size:18px;margin:0 0 8px;'>New paper alerts</h2>",
        f"<p style='color:#94a3b8;margin:0 0 12px;'>Matches among papers harvested in this {timeframe_label}.</p>",
    ]
    for block in alert_results:
        keyword = block["keyword"]
        papers = block.get("papers") or []
        text_parts.append(f'Keyword: "{keyword}" ({len(papers)} match{"es" if len(papers) != 1 else ""})')
        html_parts.append(
            f"<h3 style='color:#f8fafc;font-size:15px;margin:16px 0 6px;'>“{keyword}” "
            f"<span style='color:#94a3b8;font-weight:500;'>({len(papers)})</span></h3>"
        )
        if not papers:
            text_parts.append("  (no matches)")
            html_parts.append("<p style='color:#64748b;font-size:13px;'>No matches this period.</p>")
        else:
            html_parts.append("<ul style='padding-left:18px;color:#94a3b8;'>")
            for paper in papers[:12]:
                title = (paper.get("title") or "Untitled").strip()
                year = paper.get("year") or ""
                text_parts.append(f"  - {title}" + (f" ({year})" if year else ""))
                html_parts.append(
                    f"<li style='margin-bottom:6px;'>{title}"
                    + (f" <span style='color:#64748b;'>({year})</span>" if year else "")
                    + "</li>"
                )
            if len(papers) > 12:
                extra = len(papers) - 12
                text_parts.append(f"  …and {extra} more")
                html_parts.append(f"<li style='color:#64748b;'>…and {extra} more</li>")
            html_parts.append("</ul>")
        text_parts.append("")
    return "\n".join(text_parts), "".join(html_parts)


def _fetch_recent_papers(db, *, recent_range: str, query: Optional[str] = None, limit: int = 5000) -> List[Dict[str, Any]]:
    """Load papers harvested in the notification window, optionally filtered by keyword."""
    filters: Dict[str, Any] = {
        "recent_range": recent_range,
        "limit": limit,
        "offset": 0,
    }
    if query:
        filters["query"] = query
    return db.search_papers_for_analysis(filters)


def build_digest_for_user(db, user: Dict[str, Any], prefs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Assemble digest payload for one user, or None if there is nothing to send."""
    # Import here to avoid circular imports at module load.
    import app as app_module

    prefs = normalize_notification_preferences(prefs)
    frequency = prefs["frequency"]
    recent_range = FREQUENCY_TO_RECENT_RANGE[frequency]
    timeframe_label = {"daily": "day", "weekly": "week", "monthly": "month"}[frequency]

    alert_results = []
    for alert in prefs["keyword_alerts"]:
        if not alert.get("enabled") or not alert.get("keyword"):
            continue
        papers = _fetch_recent_papers(db, recent_range=recent_range, query=alert["keyword"], limit=200)
        alert_results.append({"keyword": alert["keyword"], "papers": papers})

    summary_papers: List[Dict[str, Any]] = []
    chart_data: Dict[str, Any] = {}
    if prefs.get("summaries_enabled"):
        summary_papers = _fetch_recent_papers(db, recent_range=recent_range, limit=10000)
        chart_data = app_module._compute_analysis_chart_data(summary_papers)
        chart_data["paper_count"] = len(summary_papers)

    has_alert_hits = any(block.get("papers") for block in alert_results)
    if not prefs.get("summaries_enabled") and not has_alert_hits:
        return None
    # If only summaries are enabled, still send (including zero-paper periods).
    if prefs.get("summaries_enabled") and not summary_papers and not has_alert_hits:
        chart_data = {"paper_count": 0, "study_design": {}, "cannabis_type": {}, "outcome": {},
                      "clinical_exposure": {}, "vitro_exposure": {}, "vivo_exposure": {}}

    text_sections = []
    html_sections = []
    alert_text, alert_html = build_keyword_alert_sections(alert_results, timeframe_label=timeframe_label)
    if alert_text:
        text_sections.append(alert_text)
        html_sections.append(alert_html)
    if prefs.get("summaries_enabled"):
        summary_text, summary_html = build_summary_sections(
            chart_data,
            summary_papers,
            timeframe_label=timeframe_label,
        )
        text_sections.append(summary_text)
        html_sections.append(summary_html)

    username = user.get("username") or "there"
    subject = f"Your {frequency} cannabis catalog digest"
    text = (
        f"Hello {username},\n\n"
        + "\n\n".join(text_sections)
        + f"\n\nOpen the catalog: {CATALOG_URL}\n"
        "Manage notification settings: "
        f"{CATALOG_URL.rstrip('/')}/settings\n"
    )
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #0b0f19; color: #f8fafc; padding: 20px;">
        <div style="max-width: 640px; margin: 0 auto; background-color: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 28px;">
          <p style="color:#94a3b8;">Hello <strong style="color:#f8fafc;">{username}</strong>,</p>
          {''.join(html_sections)}
          <p style="margin-top:24px;">
            <a href="{CATALOG_URL}" style="color:#6366f1;">Open the catalog</a>
            &nbsp;·&nbsp;
            <a href="{CATALOG_URL.rstrip('/')}/settings" style="color:#6366f1;">Notification settings</a>
          </p>
        </div>
      </body>
    </html>
    """
    return {
        "subject": subject,
        "text": text,
        "html": html,
        "alert_hit_count": sum(len(b.get("papers") or []) for b in alert_results),
        "summary_paper_count": len(summary_papers),
    }


def run_due_notification_digests(db, *, now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    """Send due notification digests for all verified users with prefs enabled."""
    now = now or datetime.now()
    if not email_service.is_email_delivery_configured():
        return {"sent": 0, "skipped": 0, "errors": 0, "reason": "email_not_configured"}

    sent = skipped = errors = 0
    users = db.list_verified_users_for_notifications()
    for user in users:
        raw_prefs = user.get("notification_preferences")
        if isinstance(raw_prefs, str):
            try:
                import json
                raw_prefs = json.loads(raw_prefs) if raw_prefs else {}
            except Exception:
                raw_prefs = {}
        prefs = normalize_notification_preferences(raw_prefs if isinstance(raw_prefs, dict) else {})
        if not force and not digest_is_due(prefs, now=now):
            skipped += 1
            continue
        has_alerts = any(a.get("enabled") and a.get("keyword") for a in prefs["keyword_alerts"])
        if not has_alerts and not prefs.get("summaries_enabled"):
            skipped += 1
            continue
        try:
            digest = build_digest_for_user(db, user, prefs)
            if not digest:
                prefs["last_digest_sent_at"] = now.isoformat(timespec="seconds")
                db.set_user_notification_preferences(int(user["id"]), prefs)
                skipped += 1
                continue
            ok = email_service.send_email(
                user["email"],
                digest["subject"],
                digest["text"],
                digest["html"],
            )
            if ok:
                prefs["last_digest_sent_at"] = now.isoformat(timespec="seconds")
                db.set_user_notification_preferences(int(user["id"]), prefs)
                sent += 1
            else:
                errors += 1
        except Exception:
            logger.exception("Failed notification digest for user %s", user.get("id"))
            errors += 1
    return {"sent": sent, "skipped": skipped, "errors": errors}
