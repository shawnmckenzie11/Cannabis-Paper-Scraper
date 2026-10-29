"""MCK-102: privacy-first Sentry wiring for the Flask app.

No-op unless SENTRY_DSN is set. Disabled when CRN_SENTRY_DISABLED=1 (scoring /
evaluation runs). Sends no PII, no request bodies, no cookies/auth headers, no
query strings, no local variables, and redacts e-mail addresses / obvious secrets.
"""
import os
import re
from urllib.parse import urlsplit, urlunsplit

_TRUTHY = {"1", "true", "yes", "on"}
_SAFE_HEADERS = {"user-agent", "content-type", "content-length", "accept", "host", "x-request-id"}
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|session|code|state)"
    r"(\s*[=:]\s*)([^\s&,;\"']+)"
)
_KEY_PATTERNS = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._-]+)")
_SENSITIVE_KEYS = re.compile(r"(?i)rationale|transcript|text|abstract|body|content|notes?\b|message|password|passwd|secret|token|api[_-]?key|auth|cookie|session|email|code|dsn")


def strip_query(url):
    """Drop ?query and #fragment from a URL string; leave non-strings alone."""
    if not isinstance(url, str) or not url:
        return url
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0]


def scrub_text(s):
    if not isinstance(s, str):
        return s
    s = _EMAIL_RE.sub("[email]", s)
    s = _KEY_PATTERNS.sub("[secret]", s)
    return _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "[filtered]", s)


def _scrub_mapping(d):
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        if _SENSITIVE_KEYS.search(str(k)):
            out[k] = "[filtered]"
        elif isinstance(v, str):
            out[k] = scrub_text(strip_query(v) if k in ("url", "http.url", "http.query") or str(v).startswith("http") else v)
        elif isinstance(v, dict):
            out[k] = _scrub_mapping(v)
        else:
            out[k] = v
    return out


def _scrub_request(req):
    if not isinstance(req, dict):
        return req
    for k in ("data", "cookies", "query_string", "env", "fragment"):
        req.pop(k, None)
    if "url" in req:
        req["url"] = strip_query(req["url"])
    headers = req.get("headers")
    if isinstance(headers, dict):
        req["headers"] = {k: v for k, v in headers.items() if k.lower() in _SAFE_HEADERS}
    return req


_SAFE_CONTEXTS = {"runtime", "os", "trace", "device", "app", "flask"}


def hash_text(kind, text):
    """Replace free text with '<kind> sha256:<12 hex>' so no content ever leaves."""
    import hashlib
    if text is None:
        text = ""
    if not isinstance(text, str):
        text = repr(text)
    return "%s sha256:%s" % (kind or "Unknown", hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12])


def before_send(event, hint=None):
    event.pop("user", None)
    event.pop("extra", None)
    if isinstance(event.get("contexts"), dict):
        event["contexts"] = {k: v for k, v in event["contexts"].items() if k in _SAFE_CONTEXTS}
    exc_values = (event.get("exception") or {}).get("values") or []
    first_type = exc_values[0].get("type") if exc_values else None
    for exc in exc_values:
        exc["value"] = hash_text(exc.get("type"), exc.get("value"))
        for fr in (exc.get("stacktrace") or {}).get("frames") or []:
            for k in ("vars", "context_line", "pre_context", "post_context"):
                fr.pop(k, None)
    for th in (event.get("threads") or {}).get("values") or []:
        for fr in (th.get("stacktrace") or {}).get("frames") or []:
            for k in ("vars", "context_line", "pre_context", "post_context"):
                fr.pop(k, None)
    kind = first_type or "LogMessage"
    if isinstance(event.get("logentry"), dict):
        le = event["logentry"]
        le["message"] = hash_text(kind, le.get("message"))
        le.pop("params", None)
        le.pop("formatted", None)
    if event.get("message") is not None:
        event["message"] = hash_text(kind, event["message"])
    if "request" in event:
        event["request"] = _scrub_request(event["request"])
    if isinstance(event.get("transaction"), str):
        event["transaction"] = strip_query(event["transaction"])
    for sp in event.get("spans") or []:
        if isinstance(sp.get("description"), str):
            sp["description"] = scrub_text(_strip_desc(sp["description"]))
        if isinstance(sp.get("data"), dict):
            sp["data"] = _scrub_mapping(sp["data"])
            sp["data"].pop("http.query", None)
            sp["data"].pop("http.fragment", None)
    bc = event.get("breadcrumbs")
    if isinstance(bc, dict) and isinstance(bc.get("values"), list):
        bc["values"] = [b for b in (before_breadcrumb(x) for x in bc["values"]) if b]
    return event


def _strip_desc(desc):
    # e.g. "GET https://api.x/y?q=1" -> "GET https://api.x/y"
    parts = desc.split(" ")
    return " ".join(strip_query(p) if "?" in p or "#" in p else p for p in parts)


before_send_transaction = before_send


def before_breadcrumb(crumb, hint=None):
    if not isinstance(crumb, dict):
        return crumb
    if isinstance(crumb.get("data"), dict):
        crumb["data"] = _scrub_mapping(crumb["data"])
        crumb["data"].pop("http.query", None)
        crumb["data"].pop("http.fragment", None)
    if crumb.get("message") is not None:
        # All breadcrumb messages (incl. logging) are hashed: never send text.
        crumb["message"] = hash_text(crumb.get("category") or crumb.get("type") or "Breadcrumb", crumb["message"])
    return crumb


def _float_env(name, default):
    try:
        return max(0.0, min(1.0, float(os.getenv(name, default))))
    except ValueError:
        return default


def sentry_enabled(env=None):
    env = os.environ if env is None else env
    if (env.get("CRN_SENTRY_DISABLED") or "").strip().lower() in _TRUTHY:
        return False
    return bool((env.get("SENTRY_DSN") or "").strip())


def init_sentry():
    """Initialise Sentry if configured. Returns True when initialised."""
    if not sentry_enabled():
        return False
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"].strip(),
        integrations=[FlaskIntegration()],
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        traces_sample_rate=_float_env("SENTRY_TRACES_SAMPLE_RATE", 0.0),
        environment=os.getenv("SENTRY_ENVIRONMENT") or os.getenv("FLY_APP_NAME") or None,
        release=os.getenv("SENTRY_RELEASE") or os.getenv("FLY_IMAGE_REF") or os.getenv("GIT_SHA") or None,
        before_send=before_send,
        before_send_transaction=before_send_transaction,
        before_breadcrumb=before_breadcrumb,
    )
    return True
