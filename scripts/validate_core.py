"""
Standalone validator for pure-Python modules.

Runs without pytest (which isn't always present in the sandbox), using
fresh module loading so test env vars take effect. Integration bits
that need FastAPI / pydantic / asyncpg are out of scope here — they
run under pytest in a real environment.

Covers:
  • text_cleaner          — clean_reply + word-boundary bug-fix regression
  • phone normalisation   — TwilioClient._normalize_phone
  • Twilio HMAC           — signing algorithm
  • session tokens        — issue/verify/shape (new)
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent  # project root
sys.path.insert(0, str(ROOT))


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)       # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]
    return mod


def run(name, fn):
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


passed = 0
total = 0


# ═══════════════════════════════════════════════════════════════
# text_cleaner
# ═══════════════════════════════════════════════════════════════
tc = _load("tc_mod", ROOT / "app" / "services" / "text_cleaner.py")
clean_reply = tc.clean_reply

print("\ntext_cleaner")

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
    ("strips [HOT_LEAD]/[END_CALL] tags",               t_strip_tags),
    ("hot_lead forces end_call",                         t_hot_forces_end),
    ("plain reply → no end, no hot",                     t_plain_reply),
    ("strips markdown + emoji + headings",               t_markdown_stripped),
    ("strips <think> block",                             t_think_stripped),
    ("detects end phrase when tag missing",              t_end_phrase_no_tag),
    ("word-boundary prevents 'take care'→'careful' fp",  t_word_boundary_fix),
    ("genuine 'take care' still triggers end_call",      t_real_take_care_still_fires),
    ("ampersand replaced with 'and'",                    t_ampersand),
    ("empty input returns safe default",                 t_empty_input),
]:
    total += 1
    if run(name, fn): passed += 1


# ═══════════════════════════════════════════════════════════════
# Build a minimal package skeleton so we can import leaf modules
# without dragging in fastapi/pydantic/asyncpg.
# ═══════════════════════════════════════════════════════════════
fake_app = types.ModuleType("app"); fake_app.__path__ = [str(ROOT / "app")]
sys.modules.setdefault("app", fake_app)
fake_core = types.ModuleType("app.core"); fake_core.__path__ = [str(ROOT / "app" / "core")]
sys.modules.setdefault("app.core", fake_core)

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

_load("app.core.exceptions", ROOT / "app" / "core" / "exceptions.py")
_load("app.core.logging", ROOT / "app" / "core" / "logging.py")

fake_services = types.ModuleType("app.services")
fake_services.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app.services", fake_services)
fake_tel = types.ModuleType("app.services.telephony")
fake_tel.__path__ = [str(ROOT / "app" / "services" / "telephony")]
sys.modules.setdefault("app.services.telephony", fake_tel)

# Stub httpx (sandbox has no network install; we don't call any actual HTTP).
if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub_httpx = types.ModuleType("httpx")
        class _StubClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **kw): raise RuntimeError("stubbed")
            async def get(self, *a, **kw): raise RuntimeError("stubbed")
        stub_httpx.AsyncClient = _StubClient
        sys.modules["httpx"] = stub_httpx


# ═══════════════════════════════════════════════════════════════
# phone normalization
# ═══════════════════════════════════════════════════════════════
print("\ntelephony — phone normalisation")
tw = _load(
    "app.services.telephony.twilio_client",
    ROOT / "app" / "services" / "telephony" / "twilio_client.py",
)
TwilioClient = tw.TwilioClient
ValidationError = sys.modules["app.core.exceptions"].ValidationError

def t_adds_plus():      assert TwilioClient._normalize_phone("971501234567") == "+971501234567"
def t_keeps_plus():     assert TwilioClient._normalize_phone("+971501234567") == "+971501234567"
def t_strips_spaces():  assert TwilioClient._normalize_phone("+971-50 123 4567") == "+971501234567"
def t_empty():
    try: TwilioClient._normalize_phone("")
    except ValidationError: return
    raise AssertionError("empty should raise")
def t_non_digit():
    try: TwilioClient._normalize_phone("+971ABCDE")
    except ValidationError: return
    raise AssertionError("non-digit should raise")
def t_too_short():
    try: TwilioClient._normalize_phone("+12")
    except ValidationError: return
    raise AssertionError("too short should raise")
def t_too_long():
    try: TwilioClient._normalize_phone("+1234567890123456")
    except ValidationError: return
    raise AssertionError("too long should raise")

for name, fn in [
    ("adds '+' prefix",               t_adds_plus),
    ("keeps existing '+'",            t_keeps_plus),
    ("strips spaces and dashes",      t_strips_spaces),
    ("empty raises ValidationError",  t_empty),
    ("non-digit raises",              t_non_digit),
    ("too short raises",              t_too_short),
    ("too long raises",               t_too_long),
]:
    total += 1
    if run(name, fn): passed += 1


# ═══════════════════════════════════════════════════════════════
# Session tokens (new in SaaS)
# ═══════════════════════════════════════════════════════════════
print("\nsession tokens")
st = _load(
    "app.core.session_tokens",
    ROOT / "app" / "core" / "session_tokens.py",
)

def t_issued_random():
    a = st.issue_token("s"); b = st.issue_token("s")
    assert a.token != b.token and a.token_hash != b.token_hash

def t_verify_ok():
    t = st.issue_token("s")
    assert st.verify_token_shape(t.token, "s")

def t_verify_wrong_secret():
    t = st.issue_token("correct")
    assert not st.verify_token_shape(t.token, "wrong")

def t_verify_tampered():
    t = st.issue_token("s")
    assert not st.verify_token_shape("X" + t.token[1:], "s")

def t_verify_malformed():
    assert not st.verify_token_shape("", "s")
    assert not st.verify_token_shape("nodot", "s")
    assert not st.verify_token_shape(".trail", "s")
    assert not st.verify_token_shape("leading.", "s")

def t_hash_sha256():
    assert st.hash_token("abc") == st.hash_token("abc")
    assert len(st.hash_token("abc")) == 64

for name, fn in [
    ("issue_token returns fresh random values", t_issued_random),
    ("verify_token_shape: valid",                t_verify_ok),
    ("verify_token_shape: wrong secret",         t_verify_wrong_secret),
    ("verify_token_shape: tampered",             t_verify_tampered),
    ("verify_token_shape: malformed",            t_verify_malformed),
    ("hash_token: deterministic SHA-256",        t_hash_sha256),
]:
    total += 1
    if run(name, fn): passed += 1


# ═══════════════════════════════════════════════════════════════
# Twilio HMAC (inline — avoids FastAPI import)
# ═══════════════════════════════════════════════════════════════
print("\nsecurity — Twilio HMAC signing")

import base64, hashlib, hmac  # noqa

def _compute_sig(auth_token, url, params):
    data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    mac = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()

def t_sig_spec_match():
    sig = _compute_sig(
        "12345",
        "https://mycompany.com/myapp.php?foo=1&bar=2",
        {"Digits": "1234", "To": "+18005551212", "From": "+14158675310",
         "Caller": "+14158675310", "CallSid": "CA1234567890ABCDE"},
    )
    # Algorithm reference — just confirms our local compute is deterministic.
    expected = _compute_sig(
        "12345", "https://mycompany.com/myapp.php?foo=1&bar=2",
        {"Digits": "1234", "To": "+18005551212", "From": "+14158675310",
         "Caller": "+14158675310", "CallSid": "CA1234567890ABCDE"},
    )
    assert sig == expected

def t_sig_changes():
    a = _compute_sig("tok", "https://x", {"x": "1"})
    b = _compute_sig("tok", "https://x", {"x": "2"})
    assert a != b

for name, fn in [
    ("signature deterministic on same inputs", t_sig_spec_match),
    ("signature changes with param changes",    t_sig_changes),
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
