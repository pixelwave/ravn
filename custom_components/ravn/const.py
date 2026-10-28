"""Constants for the RAVN integration."""

from __future__ import annotations

import re

DOMAIN = "ravn"
DEFAULT_PORT = 8770
CONF_FINGERPRINT = "fingerprint"
CONF_NODE_ID = "node_id"
CONF_NODES = "nodes"
CONF_ARCANE = "arcane"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INCLUDE_REMOTE = "include_remote"
CONF_STALE_HOURS = "stale_hours"
CONF_SHOW_UPDATES = "show_updates"
CONF_ARCANE_ENV = "arcane_env"
CONF_ARCANE_MANAGER = "arcane_manager"
CONF_ADOPT_FROM = "adopt_from"
CONF_MACHINE_NAME = "machine_name"
CONF_MACHINE_IP = "machine_ip"
CONF_AUTO_TITLE = "auto_title"

DEFAULT_ARCANE_PORT = 3552
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 3600
DEFAULT_STALE_HOURS = 24

PLATFORMS = ["sensor", "switch", "number", "update", "button", "text"]

_SLUG_BREAKS = re.compile(r"[^a-z0-9]+")


def ravn_entity_id(domain: str, *parts: str) -> str:
    """Return an entity id whose object id starts with ravn_."""
    text = " ".join(part.strip() for part in parts if part and part.strip()).lower()
    slug = _SLUG_BREAKS.sub("_", text).strip("_")
    return f"{domain}.ravn_{slug}" if slug else f"{domain}.ravn"
