import sentry_setup as ss


def test_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    assert ss.init_sentry() is False
    monkeypatch.setenv("SENTRY_DSN", "  ")
    assert ss.init_sentry() is False


def test_disabled_flag_wins(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://k@o0.ingest.sentry.io/1")
    monkeypatch.setenv("CRN_SENTRY_DISABLED", "1")
    assert ss.sentry_enabled() is False
    assert ss.init_sentry() is False


def test_strip_query():
    assert ss.strip_query("https://a.b/c/d?q=x&k=1#f") == "https://a.b/c/d"
    assert ss.strip_query("/api/search?q=cbd") == "/api/search"
    assert ss.strip_query(None) is None


def test_breadcrumb_query_stripped():
    c = ss.before_breadcrumb({"category": "httplib", "data": {"url": "https://api.semanticscholar.org/x?api_key=SEKRET", "http.query": "api_key=SEKRET", "method": "GET"}})
    assert "SEKRET" not in str(c)
    assert c["data"]["url"] == "https://api.semanticscholar.org/x"


def test_event_scrubbed():
    ev = {
        "user": {"email": "a@b.com", "ip_address": "1.2.3.4"},
        "request": {"url": "https://app/login?next=/x&email=a@b.com", "query_string": "email=a@b.com",
                    "data": {"password": "hunter2"}, "cookies": {"session": "s"},
                    "headers": {"Cookie": "session=s", "Authorization": "Bearer t", "User-Agent": "ua"}},
        "exception": {"values": [{"value": "bad login for a@b.com password=hunter2",
                                  "stacktrace": {"frames": [{"vars": {"password": "hunter2"}}]}}]},
        "spans": [{"description": "GET https://eutils.ncbi.nlm.nih.gov/x?term=cbd", "data": {"url": "https://e/x?term=cbd"}}],
        "transaction": "/api/search?q=cbd",
    }
    out = ss.before_send(ev, {})
    s = str(out)
    for bad in ("a@b.com", "hunter2", "session=s", "Bearer t", "term=cbd", "q=cbd", "1.2.3.4"):
        assert bad not in s, bad
    assert out["request"]["headers"] == {"User-Agent": "ua"}
    assert out["request"]["url"] == "https://app/login"


def test_pii_off_in_init(monkeypatch):
    captured = {}
    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    monkeypatch.setenv("SENTRY_DSN", "https://k@o0.ingest.sentry.io/1")
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    assert ss.init_sentry() is True
    assert captured["send_default_pii"] is False
    assert captured["max_request_body_size"] == "never"
    assert captured["include_local_variables"] is False
    assert captured["traces_sample_rate"] == 0.0
    assert captured["before_send"] is ss.before_send
