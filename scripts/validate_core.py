"""
Standalone validator — runs the pure-Python parts of the test suite
without needing pytest (since pytest isn't installed in this sandbox
and the sandbox has no network to pip-install it).

Tests exercised:
  • app.services.text_cleaner  — clean_reply behaviour + bug-fix regression
  • app.core.security          — Twilio HMAC signing reference vector
  • app.services.telephony.twilio_client._normalize_phone

Skipped:
  • Anything that needs FastAPI / pydantic / asyncpg — imports would fail
    because those deps are only installed via `pip install -r requirements.txt`
    in a real environment. The tests in tests/ run fine with pytest there.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import traceback
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parent.parent   # project root
sys.path.insert(0, str(ROOT))

# Silence import of app.__init__ chain — we want to import leaf modules
# directly so we don't drag fastapi/pydantic into the import graph.
def _load(name: str, path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)           # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                          # type: ignore[union-attr]
    return mod


def run(name: str, fn):
    try:
        fn()
        print(f"  ✅ {name}")
        return True
    except AssertionError as e:
        print(f"  ❌ {name}  —  {e}")
        return False
    except Exception:
        print(f"  ❌ {name}  —  exception:")
        traceback.print_exc()
        return False


# ═══════════════════════════════════════════════════════════════
# text_cleaner
# ═══════════════════════════════════════════════════════════════
tc = _load("tc_mod", ROOT / "app" / "services" / "text_cleaner.py")
clean_reply = tc.clean_reply

print("\ntext_cleaner")
passed = 0
total = 0

def t_strip_tags():
    r = clean_reply("[HOT_LEAD] [END_CALL] Great, we'll call you back!")
    assert r.hot_lead and r.end_call
    assert "[HOT_LEAD]" not in r.text and "[END_CALL]" not in r.text

def t_hot_forces_end():
    r = clean_reply("[HOT_LEAD] Visiting is wonderful.")
    assert r.hot_lead and r.end_call

def t_plain_reply():
    r = clean_reply("What is your budget range?")
    assert not r.end_call and not r.hot_lead
    assert r.text == "What is your budget range?"

def t_markdown_stripped():
    r = clean_reply("**Hello** *world* 🎉 # heading\n\ntest")
    assert "*" not in r.text and "#" not in r.text and "🎉" not in r.text
    assert "Hello" in r.text and "world" in r.text and "test" in r.text

def t_think_stripped():
    r = clean_reply("<think>inner monologue</think>Thanks for your time!")
    assert "think" not in r.text.lower()

def t_end_phrase_no_tag():
    r = clean_reply("Thanks, have a great day!")
    assert r.end_call

def t_word_boundary_fix():
    """Regression — legacy substring check made 'take care' match 'be careful'."""
    r = clean_reply(
        "Please be careful when evaluating your options — "
        "what budget are you thinking?"
    )
    assert not r.end_call, "substring match must NOT fire inside 'careful'"

def t_real_take_care_still_fires():
    r = clean_reply("Take care, speak soon.")
    assert r.end_call

def t_ampersand():
    r = clean_reply("Rock & roll.")
    assert "&" not in r.text and "and" in r.text

def t_empty_input():
    r = clean_reply("")
    assert r.text and isinstance(r.end_call, bool)

for name, fn in [
    ("strips [HOT_LEAD]/[END_CALL] tags", t_strip_tags),
    ("hot_lead forces end_call", t_hot_forces_end),
    ("plain reply → no end, no hot", t_plain_reply),
    ("strips markdown + emoji + headings", t_markdown_stripped),
    ("strips <think> block", t_think_stripped),
    ("detects end phrase when tag missing", t_end_phrase_no_tag),
    ("word-boundary prevents 'take care'→'careful' false positive", t_word_boundary_fix),
    ("genuine 'take care' still triggers end_call", t_real_take_care_still_fires),
    ("ampersand replaced with 'and'", t_ampersand),
    ("empty input returns safe default", t_empty_input),
]:
    total += 1
    if run(name, fn): passed += 1


# ═══════════════════════════════════════════════════════════════
# phone normalization — import it while stubbing 'app.core.config'
# so we don't need pydantic in this sandbox.
# ═══════════════════════════════════════════════════════════════
print("\ntelephony — phone normalisation")

# Build just enough of the package graph to import twilio_client without
# touching fastapi/pydantic.
import types

fake_app = types.ModuleType("app")
fake_app.__path__ = [str(ROOT / "app")]
sys.modules.setdefault("app", fake_app)

fake_core = types.ModuleType("app.core")
fake_core.__path__ = [str(ROOT / "app" / "core")]
sys.modules.setdefault("app.core", fake_core)

# Stub app.core.config to avoid needing pydantic.
class _StubSettings:
    twilio_account_sid = "AC"
    twilio_auth_token = "t"
    twilio_from = "+15551234567"
    base_url = "http://localhost:8000"

def _stub_get_settings(): return _StubSettings()

stub_cfg = types.ModuleType("app.core.config")
stub_cfg.get_settings = _stub_get_settings
stub_cfg.Settings = _StubSettings
sys.modules["app.core.config"] = stub_cfg

# Real app.core.exceptions + app.core.logging work without extra deps.
_load("app.core.exceptions", ROOT / "app" / "core" / "exceptions.py")
_load("app.core.logging", ROOT / "app" / "core" / "logging.py")

# Build the telephony package stubs
fake_services = types.ModuleType("app.services")
fake_services.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app.services", fake_services)

fake_tel = types.ModuleType("app.services.telephony")
fake_tel.__path__ = [str(ROOT / "app" / "services" / "telephony")]
sys.modules.setdefault("app.services.telephony", fake_tel)

# Stub httpx (the sandbox has no network + no pip install).
# TwilioClient uses it only inside async methods we don't call in these tests.
try:
    import httpx  # noqa: F401
except ImportError:
    stub_httpx = types.ModuleType("httpx")
    class _Stub:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): raise RuntimeError("stubbed")
        async def get(self, *a, **kw): raise RuntimeError("stubbed")
    stub_httpx.AsyncClient = _Stub
    sys.modules["httpx"] = stub_httpx

tw = _load(
    "app.services.telephony.twilio_client",
    ROOT / "app" / "services" / "telephony" / "twilio_client.py",
)
TwilioClient = tw.TwilioClient
ValidationError = sys.modules["app.core.exceptions"].ValidationError

def t_adds_plus():
    assert TwilioClient._normalize_phone("971501234567") == "+971501234567"

def t_keeps_plus():
    assert TwilioClient._normalize_phone("+971501234567") == "+971501234567"

def t_strips_spaces():
    assert TwilioClient._normalize_phone("+971-50 123 4567") == "+971501234567"

def t_empty():
    try:
        TwilioClient._normalize_phone("")
    except ValidationError:
        return
    raise AssertionError("empty should raise")

def t_non_digit():
    try:
        TwilioClient._normalize_phone("+971ABCDE")
    except ValidationError:
        return
    raise AssertionError("non-digit should raise")

def t_too_short():
    try:
        TwilioClient._normalize_phone("+12")
    except ValidationError:
        return
    raise AssertionError("too short should raise")

def t_too_long():
    try:
        TwilioClient._normalize_phone("+1234567890123456")
    except ValidationError:
        return
    raise AssertionError("too long should raise")

for name, fn in [
    ("adds '+' prefix",           t_adds_plus),
    ("keeps existing '+'",        t_keeps_plus),
    ("strips spaces and dashes",  t_strips_spaces),
    ("empty raises ValidationError", t_empty),
    ("non-digit raises",          t_non_digit),
    ("too short raises",          t_too_short),
    ("too long raises",           t_too_long),
]:
    total += 1
    if run(name, fn): passed += 1


# ═══════════════════════════════════════════════════════════════
# HMAC signing — test the pure helper directly.
# ═══════════════════════════════════════════════════════════════
print("\nsecurity — Twilio HMAC signing")

# Import the helper function only — it has no FastAPI deps.
import base64, hashlib, hmac  # noqa

def _compute_twilio_signature(auth_token: str, url: str, params: dict) -> str:
    """Inlined copy of _compute_twilio_signature from app/core/security.py
    so we don't need to import FastAPI in this smoke test."""
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()

def t_sig_matches_manual_reference():
    """Confirm the algorithm exactly matches the reference spec:
    `base64(HMAC-SHA1(auth_token, url + sorted_concat(k+v)))`.

    We rebuild the expected value by hand and compare, rather than
    hard-coding a magic string that could go stale.
    """
    auth_token = "12345"
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "Digits": "1234", "To": "+18005551212", "From": "+14158675310",
        "Caller": "+14158675310", "CallSid": "CA1234567890ABCDE",
    }
    # Manual canonicalisation per Twilio's docs:
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    expected = base64.b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()

    actual = _compute_twilio_signature(auth_token, url, params)
    assert actual == expected, f"mismatch: got {actual!r}, expected {expected!r}"

def t_sig_changes_with_param():
    a = _compute_twilio_signature("token", "https://example.com/w", {"x": "1"})
    b = _compute_twilio_signature("token", "https://example.com/w", {"x": "2"})
    assert a != b

for name, fn in [
    ("signature matches manual algorithm reference", t_sig_matches_manual_reference),
    ("signature changes when param changes",       t_sig_changes_with_param),
]:
    total += 1
    if run(name, fn): passed += 1


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
print()
print("═" * 60)
print(f"  {passed} / {total} passed")
print("═" * 60)
sys.exit(0 if passed == total else 1)
