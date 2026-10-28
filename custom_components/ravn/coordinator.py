"""Polling coordinator. One request per interval returns every enabled sensor."""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .arcane import (
    ArcaneAuthError,
    ArcaneClient,
    ArcaneError,
    container_note_record,
    drop_verified_missing,
    merge_presence,
    prune_container_notes,
)
from .client import RavnAuthError, RavnClient, RavnError
from .const import CONF_ARCANE, CONF_STALE_HOURS, DEFAULT_STALE_HOURS, DOMAIN

_LOGGER = logging.getLogger(__name__)
CHECK_EVERY = 6 * 60 * 60


def poll_interval(data: dict[str, Any]) -> timedelta:
    """Use the shortest enabled sensor interval, or five minutes when none are on."""
    intervals: list[int] = []
    for sensor in data.get("catalog") or []:
        if sensor.get("controllable") and sensor.get("enabled"):
            intervals.append(int(sensor.get("interval_s") or 300))
    if not intervals:
        return timedelta(seconds=300)
    return timedelta(seconds=max(1, min(intervals)))


class RavnCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch snapshots and occasionally ask the agent to check for a release."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: RavnClient,
        node: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"RAVN {node['host']}",
            update_interval=timedelta(seconds=300),
        )
        self.client = client
        self.node_id = str(node["node_id"])
        self.host = str(node["host"])
        self.port = int(node["port"])
        self._checked: float | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.readings()
        except RavnAuthError as err:
            raise UpdateFailed("token was rejected") from err
        except RavnError as err:
            raise UpdateFailed(str(err)) from err

        update = data.get("update") or {}
        latest = update.get("latest") or None
        notes = update.get("notes") or None
        if not latest and self.data:
            latest = self.data.get("latest_version")
            notes = notes or self.data.get("release_notes")
        data["latest_version"] = latest
        data["release_notes"] = notes
        now = time.monotonic()
        if not latest or self._checked is None or now - self._checked >= CHECK_EVERY:
            try:
                info = await self.client.update_check()
            except RavnError:
                info = None
            else:
                self._checked = now
            if info and info.get("latest"):
                data["latest_version"] = info["latest"]
                data["release_notes"] = info.get("notes")
        self.update_interval = poll_interval(data)
        return data


class ArcaneCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll one Arcane manager. A rejected key does not unload the RAVN nodes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ArcaneClient,
        scan_interval: int,
        include_remote: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Arcane",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.url = client.url
        self.include_remote = include_remote
        self._missing: dict[str, float] = {}
        self._missing_loaded = False
        self._store: Store[dict[str, float]] = Store(hass, 1, f"{DOMAIN}.arcane-missing.{entry.entry_id}")
        self._notes: dict[str, dict[str, str]] = {}
        self._notes_loaded = False
        self._notes_store: Store[dict[str, dict[str, str]]] = Store(
            hass, 1, f"{DOMAIN}.arcane-notes.{entry.entry_id}"
        )

    def _grace_seconds(self) -> float | None:
        """Hours to keep a vanished container. Zero or less means keep it until cleanup."""
        raw = self.config_entry.data.get(CONF_ARCANE)
        hours: Any = DEFAULT_STALE_HOURS
        if isinstance(raw, dict) and CONF_STALE_HOURS in raw:
            hours = raw.get(CONF_STALE_HOURS)
        try:
            value = float(hours)
        except (TypeError, ValueError):
            value = float(DEFAULT_STALE_HOURS)
        if value <= 0:
            return None
        return value * 3600

    async def _load_missing(self) -> None:
        if self._missing_loaded:
            return
        self._missing_loaded = True
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        loaded: dict[str, float] = {}
        for key, stamp in stored.items():
            try:
                loaded[str(key)] = float(stamp)
            except (TypeError, ValueError):
                continue
        self._missing = loaded

    async def _store_missing(self, missing: dict[str, float]) -> None:
        if missing == self._missing:
            return
        self._missing = missing
        await self._store.async_save(missing)

    async def _async_update_data(self) -> dict[str, Any]:
        await self._load_missing()
        await self._load_notes()
        try:
            current = await self.client.snapshot(include_remote=self.include_remote)
        except ArcaneAuthError as err:
            raise UpdateFailed("API key was rejected") from err
        except ArcaneError as err:
            raise UpdateFailed(str(err)) from err
        merged, missing = merge_presence(
            self.data,
            current,
            self._missing,
            now=time.time(),
            grace_seconds=self._grace_seconds(),
            force_drop=False,
        )
        await self._store_missing(missing)
        await self._prune_notes(merged)
        return merged

    def container_notes(self, key: str) -> dict[str, Any]:
        """Custom URL, name, and exclude flag. Missing strings are empty."""
        raw = self._notes.get(key) or {}
        return {
            "url": str(raw.get("url") or ""),
            "name": str(raw.get("name") or ""),
            "exclude": bool(raw.get("exclude")),
        }

    async def async_set_container_note(self, key: str, field: str, value: str) -> None:
        """Save one custom field and refresh entities that show it."""
        await self._load_notes()
        current = self.container_notes(key)
        if field == "exclude":
            current["exclude"] = str(value).strip().lower() in {"1", "true", "on", "yes"}
        else:
            current[field] = value
        record = container_note_record(current["url"], current["name"], current["exclude"])
        notes = dict(self._notes)
        if record is None:
            notes.pop(key, None)
        else:
            notes[key] = record
        if notes != self._notes:
            self._notes = notes
            await self._notes_store.async_save(notes)
        if self.data is not None:
            self.async_set_updated_data(self.data)

    async def _load_notes(self) -> None:
        if self._notes_loaded:
            return
        self._notes_loaded = True
        stored = await self._notes_store.async_load()
        if not isinstance(stored, dict):
            return
        loaded: dict[str, dict[str, str]] = {}
        for key, raw in stored.items():
            if not isinstance(raw, dict):
                continue
            record = container_note_record(
                str(raw.get("url") or ""),
                str(raw.get("name") or ""),
                bool(raw.get("exclude")),
            )
            if record is not None:
                loaded[str(key)] = record
        self._notes = loaded

    async def _prune_notes(self, snapshot: dict[str, Any]) -> None:
        await self._load_notes()
        pruned = prune_container_notes(self._notes, snapshot)
        if pruned == self._notes:
            return
        self._notes = pruned
        await self._notes_store.async_save(pruned)

    async def async_cleanup_now(self) -> None:
        """Remove containers already known to be gone. Offline machines stay."""
        if not self.data:
            return
        self._missing = {}
        await self._store.async_save({})
        cleaned = drop_verified_missing(self.data)
        await self._prune_notes(cleaned)
        self.async_set_updated_data(cleaned)
