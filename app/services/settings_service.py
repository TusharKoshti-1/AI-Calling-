"""
app/services/settings_service.py
Runtime settings manager — loads from DB on startup, cached in memory,
updated via API. Single source of truth for all customisable values.
"""
from app.core.logging import get_logger
from app.core.config import settings as cfg

log = get_logger(__name__)

# Runtime cache — updated from DB on startup and when user saves settings
_cache: dict = {
    "agent.name":         cfg.DEFAULT_AGENT_NAME,
    "agent.agency_name":  cfg.DEFAULT_AGENCY_NAME,
    "agent.language":     cfg.DEFAULT_LANGUAGE,
    "agent.intro_text":   "",
    "agent.system_prompt": "default",
    "call.speech_timeout": "3",
    "call.record":         "true",
}


async def load_from_db() -> None:
    """Load all settings from DB into cache. Call on app startup."""
    try:
        from db.repositories.settings import get_all_settings
        db_settings = await get_all_settings()
        _cache.update(db_settings)
        log.info(f"Settings loaded: agent={get('agent.name')} agency={get('agent.agency_name')}")
    except Exception as e:
        log.warning(f"Could not load settings from DB (using defaults): {e}")


def get(key: str, default: str = "") -> str:
    """Get a setting value from runtime cache."""
    return _cache.get(key, default)


def get_all() -> dict:
    """Return full settings dict."""
    return dict(_cache)


async def save(key: str, value: str) -> None:
    """Save a single setting to DB and update cache."""
    try:
        from db.repositories.settings import set_setting
        await set_setting(key, value)
    except Exception as e:
        log.error(f"DB save setting error [{key}]: {e}")
    _cache[key] = value
    log.info(f"Setting saved: {key}={value[:80]}")


async def save_many(updates: dict) -> None:
    """Save multiple settings at once."""
    for key, value in updates.items():
        await save(key, str(value))


def get_agent_name() -> str:
    return get("agent.name", cfg.DEFAULT_AGENT_NAME)


def get_agency_name() -> str:
    return get("agent.agency_name", cfg.DEFAULT_AGENCY_NAME)


def get_intro_text() -> str:
    """Build intro text — use custom or auto-generate from agent/agency name."""
    custom = get("agent.intro_text", "").strip()
    if custom:
        return custom
    name   = get_agent_name()
    agency = get_agency_name()
    return (
        f"Hello, this is {name} calling from {agency}. "
        f"You recently inquired about one of our properties — "
        f"I just wanted to follow up quickly. Do you have two minutes?"
    )


def get_system_prompt() -> str:
    return get("agent.system_prompt", "default")


def invalidate_intro_cache() -> None:
    """Call after intro text or agent name changes."""
    from app.core.state import call_store
    call_store.clear_intro_cache()
    log.info("Intro audio cache invalidated")
