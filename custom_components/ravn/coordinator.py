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

from .arcane import ArcaneAuthError, ArcaneClient, ArcaneError, drop_verified_missing, merge_presence
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
        return merged

    async def async_cleanup_now(self) -> None:
        """Remove containers already known to be gone. Offline machines stay."""
        if not self.data:
            return
        self._missing = {}
        await self._store.async_save({})
        self.async_set_updated_data(drop_verified_missing(self.data))
