import sentry_setup as ss


def test_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("CRN_SENTRY_DSN", raising=False)
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    assert ss.init_sentry() is False
    monkeypatch.setenv("CRN_SENTRY_DSN", "  ")
    assert ss.init_sentry() is False


def test_disabled_flag_wins(monkeypatch):
    monkeypatch.setenv("CRN_SENTRY_DSN", "https://k@o0.ingest.sentry.io/1")
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
    assert "stacktrace" in out["exception"]["values"][0]
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_pii_off_in_init(monkeypatch):
    captured = {}
    import sentry_sdk
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    monkeypatch.setenv("CRN_SENTRY_DSN", "https://k@o0.ingest.sentry.io/1")
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "fake-app")  # Sentry reports only from a deployed app
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    assert ss.init_sentry() is True
    assert captured["send_default_pii"] is False
    assert captured["max_request_body_size"] == "never"
    assert captured["include_local_variables"] is False
    assert captured["traces_sample_rate"] == 0.0
    assert captured["before_send"] is ss.before_send
    names = {type(i).__name__ for i in captured["disabled_integrations"]}
    assert "AnthropicIntegration" in names
    try:  # importable only when the openai package is installed
        import sentry_sdk.integrations.openai  # noqa: F401
        assert "OpenAIIntegration" in names
    except Exception:
        pass


TRANSCRIPT = "raw_transcript: Reviewer said the RCT dosing was 10mg THC; rationale_text=because blinding failed"


def _frames_event(exc):
    import sentry_sdk.utils as u
    import sys
    try:
        raise exc
    except Exception:
        ei = sys.exc_info()
    from sentry_sdk.utils import event_from_exception
    ev, hint = event_from_exception(ei, client_options={"include_local_variables": True, "include_source_context": True, "max_value_length": 1024})
    return ev, hint


def test_exception_message_only_type_and_hash():
    import hashlib
    ev, hint = _frames_event(ValueError(TRANSCRIPT))
    ev["extra"] = {"rationale_text": TRANSCRIPT}
    ev["contexts"] = {"runtime": {"name": "CPython"}, "form": {"raw_transcript": TRANSCRIPT}, "trace": {"trace_id": "x"}}
    out = ss.before_send(ev, hint)
    s = str(out)
    assert "Reviewer" not in s and "blinding" not in s and "10mg" not in s
    exp = "ValueError sha256:" + hashlib.sha256(TRANSCRIPT.encode()).hexdigest()[:12]
    val = out["exception"]["values"][0]
    assert val["value"] == exp and val["type"] == "ValueError"
    fr = val["stacktrace"]["frames"][-1]
    assert fr["function"] == "_frames_event" and fr["lineno"] and fr["filename"]
    assert "vars" not in fr and "context_line" not in fr
    assert "extra" not in out
    assert set(out["contexts"]) == {"runtime", "trace"}


def test_log_and_breadcrumb_messages_dropped():
    ev = {"logentry": {"message": "saving %s", "params": [TRANSCRIPT], "formatted": "saving " + TRANSCRIPT},
          "message": "saving " + TRANSCRIPT,
          "breadcrumbs": {"values": [{"category": "app.log", "type": "log", "message": TRANSCRIPT}]}}
    out = ss.before_send(ev, {})
    s = str(out)
    assert "Reviewer" not in s and "blinding" not in s
    assert "logentry" not in out and "message" not in out
    assert "message" not in out["breadcrumbs"]["values"][0]
    assert "sha256" not in s
    c = ss.before_breadcrumb({"category": "root", "message": TRANSCRIPT, "data": {"rationale_text": TRANSCRIPT}})
    assert "Reviewer" not in str(c) and "message" not in c


def test_malformed_event_tolerated():
    ev = {
        "spans": [None, {"description": None, "data": None}],
        "exception": {"values": [None, {"type": "E", "value": "x", "stacktrace": {"frames": [None, {"vars": {"a": 1}}]}}]},
        "threads": {"values": [None, {"stacktrace": None}]},
        "breadcrumbs": {"values": [None, {"message": "m", "data": None}]},
        "tags": None,
    }
    out = ss.before_send(ev)
    assert out is not None
    assert "vars" not in out["exception"]["values"][1]["stacktrace"]["frames"][1]
    assert out["breadcrumbs"]["values"] == [{"data": None}]
    assert ss.before_breadcrumb(None) is None

    class Boom(dict):
        def pop(self, *a):
            raise RuntimeError("boom")
    assert ss.before_send(Boom()) is None
    assert ss.before_breadcrumb(Boom(data={})) is None


def test_tags_allowlisted():
    ev = {"tags": {"environment": "prod", "release": "r1", "url": "https://h/p/q?email=a@b.c",
                   "user_email": "a@b.c", "paper_title": "secret", "level": "error"}}
    out = ss.before_send(ev)
    assert out["tags"] == {"environment": "prod", "release": "r1", "url": "/p/q", "level": "error"}
    out = ss.before_send({"tags": [["os", "linux"], ["token", "x"]]})
    assert out["tags"] == {"os": "linux"}


def test_harmless_keys_kept():
    out = ss.before_breadcrumb({"data": {"status_code": 500, "Content-Length": "12",
                                         "http.response.status_code": 200, "token": "t"}})
    assert out["data"]["status_code"] == 500
    assert out["data"]["Content-Length"] == "12"
    assert out["data"]["http.response.status_code"] == 200
    assert out["data"]["token"] == "[filtered]"

def test_tag_values_scrubbed():
    from sentry_setup import _scrub_tags
    d = {"url": "https://x.test/reset/a@b.com?q=1", "transaction": "/u/sk-abcdefghijk?x=1",
         "release": {"t": "secret"}, "level": 5, "environment": "prod"}
    for tags in (d, [[k, v] for k, v in d.items()]):
        out = _scrub_tags(tags)
        assert out == {"url": "/reset/[email]", "transaction": "/u/[secret]", "environment": "prod"}


FAKE_DSN = "https://fakepublickey@example.invalid/0"


def test_tags_with_unhashable_key_regression():
    # sentry_setup.py:99 used to raise TypeError (unhashable list key), so the
    # whole event was dropped by before_send's catch-all.
    tags = [[["not", "hashable"], "x"], ["environment", "test"], [{"k": 1}, "y"], ["level", "error"]]
    assert ss._scrub_tags(tags) == {"environment": "test", "level": "error"}
    ev = ss.before_send({"tags": [[["bad"], "x"], ["environment", "test"]],
                         "request": {"url": "https://app.example/api/search?q=fake"}})
    assert ev is not None
    assert ev["tags"] == {"environment": "test"}
    assert ev["request"]["url"] == "https://app.example/api/search"


def test_non_string_tag_keys_dropped():
    assert ss._scrub_tags({1: "x", ("a",): "y", "release": "r1"}) == {"release": "r1"}


def test_header_list_form_scrubbed():
    ev = ss.before_send({"request": {"headers": [["Authorization", "Bearer faketoken"], ["Cookie", "session=fake"],
                                                 ["User-Agent", "ua"], [["x"], "y"]]}})
    assert ev["request"]["headers"] == {"User-Agent": "ua"}


def test_userinfo_stripped_from_urls():
    assert ss.strip_query("https://fakeuser:fakepass@host.example/p?q=1") == "https://host.example/p"


def test_init_only_when_dsn_set(monkeypatch):
    import sentry_sdk
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "fake-app")  # Sentry reports only from a deployed app
    monkeypatch.delenv("CRN_SENTRY_DSN", raising=False)
    assert ss.init_sentry() is False
    assert calls == []
    monkeypatch.setenv("CRN_SENTRY_DSN", FAKE_DSN)
    assert ss.init_sentry() is True
    assert len(calls) == 1 and calls[0]["dsn"] == FAKE_DSN


def test_environment_from_app_env(monkeypatch):
    import sentry_sdk
    captured = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    monkeypatch.setenv("CRN_SENTRY_DSN", FAKE_DSN)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "other")
    assert ss.init_sentry() is True
    assert captured["environment"] == "staging"
    assert ss.sentry_environment({}) is None
    assert ss.sentry_environment({"APP_ENV": " ", "SENTRY_ENVIRONMENT": "prod"}) == "prod"


def test_end_to_end_no_pii_and_no_network(monkeypatch):
    """Real SDK + Flask request; a capturing transport replaces the network."""
    import sentry_sdk
    from flask import Flask
    envelopes = []

    class CaptureTransport(sentry_sdk.transport.Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

        def flush(self, *a, **k):
            pass

        def kill(self):
            pass

    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(transport=CaptureTransport, **kw))
    monkeypatch.delenv("CRN_SENTRY_DISABLED", raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "fake-app")  # Sentry reports only from a deployed app
    monkeypatch.setenv("CRN_SENTRY_DSN", FAKE_DSN)
    monkeypatch.setenv("APP_ENV", "test")
    try:
        assert ss.init_sentry() is True
        app = Flask("fake_sentry_app")

        @app.route("/boom", methods=["POST"])
        def boom():
            raise RuntimeError("fail for fake-user@example.invalid")

        client = app.test_client()
        client.set_cookie("session", "fakesessioncookie")
        client.post("/boom?q=fakequery&email=fake-user@example.invalid",
                    data={"password": "fakepassword"},
                    headers={"Authorization": "Bearer fakebearertoken", "X-Forwarded-For": "203.0.113.9"})
        sentry_sdk.flush()
    finally:
        client = sentry_sdk.get_client()
        client.flush(timeout=2)
        client.close(timeout=2)
        real_init(dsn=None)
    payload = "".join(
        item.payload.get_bytes().decode("utf-8", "replace") for env in envelopes for item in env.items
    )
    assert "RuntimeError" in payload  # the error was captured...
    for leaked in ("fakequery", "fake-user@example.invalid", "fakepassword", "fakesessioncookie",
                   "fakebearertoken", "203.0.113.9", "127.0.0.1"):
        assert leaked not in payload, leaked  # ...without PII
    assert '"environment":"test"' in payload.replace(" ", "")
