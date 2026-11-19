"""Pruebas de seguridad: token, origen y límite de peticiones."""

from __future__ import annotations

from app.security import RateLimiter, RequestGuard, compile_origin_regex

TOKEN = "token-secreto-123"


def make_guard(origins=("chrome-extension://*",), auth_required=True):
    return RequestGuard(TOKEN, origins, auth_required)


# ---------------------------------------------------------------- token
def test_token_correct_header():
    guard = make_guard()
    ok, reason = guard.token_ok("Bearer " + TOKEN, None)
    assert ok, reason


def test_token_in_body():
    guard = make_guard()
    ok, reason = guard.token_ok(None, TOKEN)
    assert ok, reason


def test_token_missing():
    guard = make_guard()
    ok, reason = guard.token_ok(None, None)
    assert not ok
    assert "Falta" in reason


def test_token_wrong():
    guard = make_guard()
    ok, reason = guard.token_ok("Bearer equivocado", None)
    assert not ok
    assert "inválido" in reason.lower()


def test_token_case_sensitive():
    guard = make_guard()
    ok, _ = guard.token_ok("Bearer " + TOKEN.upper(), None)
    assert not ok


def test_auth_disabled_skips_token():
    guard = make_guard(auth_required=False)
    ok, _ = guard.token_ok(None, None)
    assert ok


# ---------------------------------------------------------------- origin
def test_origin_wildcard_extension():
    guard = make_guard()
    ok, _ = guard.origin_ok("chrome-extension://abcdefghijklmnop")
    assert ok


def test_origin_web_rejected():
    guard = make_guard()
    ok, reason = guard.origin_ok("https://malicioso.example.com")
    assert not ok
    assert "no autorizado" in reason.lower()


def test_origin_none_is_non_browser():
    guard = make_guard()
    ok, _ = guard.origin_ok(None)
    assert ok


def test_origin_exact_id_only():
    guard = RequestGuard(TOKEN, ["chrome-extension://abcdefghijklmnop"], True)
    assert guard.origin_ok("chrome-extension://abcdefghijklmnop")[0]
    assert not guard.origin_ok("chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzz")[0]


def test_compile_origin_regex_case_insensitive():
    regex = compile_origin_regex("Chrome-Extension://*")
    assert regex.match("chrome-extension://abc") is not None


# ---------------------------------------------------------------- rate
def test_rate_limiter_blocks_after_limit():
    limiter = RateLimiter(3)
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False
    assert limiter.retry_after("ip") >= 1


def test_rate_limiter_independent_keys():
    limiter = RateLimiter(1)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True
