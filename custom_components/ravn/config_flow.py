"""Config flow for RAVN."""

from __future__ import annotations

from ipaddress import IPv4Address
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .arcane import ArcaneAuthError, ArcaneClient, ArcaneError, normalize_url
from .client import (
    RavnAuthError,
    RavnClient,
    RavnConnectError,
    RavnError,
    normalize_fingerprint,
    probe_fingerprint,
)
from .const import (
    CONF_ADOPT_FROM,
    CONF_API_KEY,
    CONF_ARCANE,
    CONF_ARCANE_ENV,
    CONF_ARCANE_MANAGER,
    CONF_AUTO_TITLE,
    CONF_FINGERPRINT,
    CONF_INCLUDE_REMOTE,
    CONF_MACHINE_IP,
    CONF_MACHINE_NAME,
    CONF_NODE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_UPDATES,
    CONF_STALE_HOURS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_HOURS,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .nodes import (
    entry_for_node,
    entry_nodes,
    entry_payload,
    find_entry_for_machine,
    find_node,
    machine_keys,
    manager_entry,
    node_dict,
    with_arcane,
    with_node,
)


def _display_host(info: ZeroconfServiceInfo) -> str:
    """Prefer an IPv4 address for the discovery title and the stored host."""
    for address in info.ip_addresses:
        if isinstance(address, IPv4Address):
            return str(address)
    return info.host


def _property(info: ZeroconfServiceInfo, key: str) -> str | None:
    value = info.properties.get(key)
    if value is None:
        value = info.properties.get(key.encode())
    if isinstance(value, bytes):
        return value.decode()
    if value is None:
        return None
    return str(value)


def _arcane_schema(current: dict[str, Any], *, key_required: bool, allow_clear: bool) -> vol.Schema:
    """URL, API key, poll interval, and whether remote machines are listed."""
    key = vol.Required(CONF_API_KEY) if key_required else vol.Optional(CONF_API_KEY)
    fields: dict[Any, Any] = {
        vol.Required(CONF_URL, default=current.get("url") or ""): str,
        key: str,
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=int(current.get("scan_interval") or DEFAULT_SCAN_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
        vol.Required(
            CONF_INCLUDE_REMOTE,
            default=True if "include_remote" not in current else bool(current["include_remote"]),
        ): bool,
        vol.Required(
            CONF_SHOW_UPDATES,
            default=bool(current.get(CONF_SHOW_UPDATES, False)),
        ): bool,
        vol.Required(
            CONF_STALE_HOURS,
            default=int(current[CONF_STALE_HOURS]) if CONF_STALE_HOURS in current else DEFAULT_STALE_HOURS,
        ): vol.All(vol.Coerce(int), vol.Range(min=0, max=720)),
    }
    if allow_clear:
        fields[vol.Required("remove", default=False)] = bool
    return vol.Schema(fields)


async def _validate_arcane(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    existing: dict[str, Any] | None,
    *,
    allow_clear: bool,
) -> tuple[str | None, dict[str, Any] | None]:
    """Check the manager. An empty URL clears a saved manager when allow_clear is set."""
    url = str(user_input.get(CONF_URL) or "").strip()
    if not url:
        if allow_clear:
            return None, None
        return "invalid_url", None
    try:
        url = normalize_url(url)
    except ValueError:
        return "invalid_url", None
    key = str(user_input.get(CONF_API_KEY) or "").strip()
    if not key and existing:
        key = str(existing.get("api_key") or "")
    if not key:
        return "invalid_auth", None
    include_remote = bool(user_input.get(CONF_INCLUDE_REMOTE))
    client = ArcaneClient(async_get_clientsession(hass), url, key)
    try:
        await client.check(include_remote=include_remote)
    except ArcaneAuthError:
        return "invalid_auth", None
    except ArcaneError:
        return "cannot_connect", None
    return None, {
        "url": client.url,
        "api_key": key,
        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
        CONF_INCLUDE_REMOTE: include_remote,
        CONF_SHOW_UPDATES: bool(user_input.get(CONF_SHOW_UPDATES, False)),
        CONF_STALE_HOURS: int(user_input.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)),
    }


class RavnConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover a node, pin its certificate, then ask for the token."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port = DEFAULT_PORT
        self._fingerprint: str | None = None
        self._name: str | None = None
        self._node_id: str | None = None

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle an mDNS advertisement."""
        node_id = _property(discovery_info, "id")
        fingerprint = _property(discovery_info, "fp")
        if not node_id or not fingerprint:
            return self.async_abort(reason="not_ravn")
        existing = await self.async_set_unique_id(node_id)
        if existing is not None and existing.source == SOURCE_IGNORE:
            return self.async_abort(reason="already_configured")
        self._host = _display_host(discovery_info)
        self._port = discovery_info.port or DEFAULT_PORT
        known = self._abort_known(node_id)
        if known is not None:
            return known
        self._fingerprint = normalize_fingerprint(fingerprint)
        hostname = discovery_info.name.split(".")[0]
        self._name = hostname
        if self._host and self._host != hostname:
            self._name = f"{hostname} • {self._host}"
        try:
            seen = await probe_fingerprint(self._host, self._port)
        except RavnConnectError:
            return self.async_abort(reason="cannot_connect")
        if seen != self._fingerprint:
            return self.async_abort(reason="fingerprint_mismatch")
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_token()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a RAVN node or an Arcane manager."""
        return self.async_show_menu(step_id="user", menu_options=["node", "arcane"])

    async def async_step_machine(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create one machine entry. Called by setup, not shown as a form."""
        info = user_input or {}
        node = info.get("node")
        nodes = [node_dict(node)] if isinstance(node, dict) else []
        env_id = str(info.get(CONF_ARCANE_ENV) or "")
        unique_id = nodes[0][CONF_NODE_ID] if nodes else f"arcane:{env_id}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        data = entry_payload(info, nodes)
        if info.get(CONF_ADOPT_FROM):
            data[CONF_ADOPT_FROM] = info[CONF_ADOPT_FROM]
        title = str(info.get("title") or info.get(CONF_AUTO_TITLE) or unique_id)
        return self.async_create_entry(title=title, data=data)

    async def async_step_node(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a host, then read its certificate before requesting the token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            try:
                fingerprint = await probe_fingerprint(host, port)
            except RavnConnectError:
                errors["base"] = "cannot_connect"
            else:
                self._host = host
                self._port = port
                self._fingerprint = fingerprint
                self._name = host
                return await self.async_step_token()
        return self.async_show_form(
            step_id="node",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the token and store this machine as its own entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = RavnClient(
                async_get_clientsession(self.hass),
                self._host or "",
                self._port,
                user_input[CONF_TOKEN].strip(),
                self._fingerprint or "",
            )
            try:
                identity = await client.identity()
            except RavnAuthError:
                errors["base"] = "invalid_auth"
            except RavnError:
                errors["base"] = "cannot_connect"
            else:
                node_id = identity.get("node_id")
                if not node_id:
                    errors["base"] = "cannot_connect"
                else:
                    return await self._async_store_node(
                        {
                            CONF_NODE_ID: str(node_id),
                            CONF_HOST: self._host,
                            CONF_PORT: self._port,
                            CONF_TOKEN: user_input[CONF_TOKEN].strip(),
                            CONF_FINGERPRINT: self._fingerprint,
                        }
                    )
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            description_placeholders={
                "name": self._name or self._host or "node",
                "fingerprint": self._fingerprint or "",
            },
            errors=errors,
        )

    async def async_step_arcane(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Arcane manager URL and API key."""
        errors: dict[str, str] = {}
        manager = manager_entry(self.hass)
        existing = manager.data.get(CONF_ARCANE) if manager is not None else None
        if not isinstance(existing, dict):
            existing = None
        if user_input is not None:
            error, arcane = await _validate_arcane(self.hass, user_input, existing, allow_clear=False)
            if error or arcane is None:
                errors["base"] = error or "invalid_url"
            else:
                return await self._async_store_arcane(arcane)
        return self.async_show_form(
            step_id="arcane",
            data_schema=_arcane_schema(existing or {}, key_required=existing is None, allow_clear=False),
            errors=errors,
        )

    async def _async_store_arcane(self, arcane: dict[str, Any]) -> ConfigFlowResult:
        """Save the manager on the machine it runs on, or create that entry."""
        manager = manager_entry(self.hass)
        if manager is not None:
            return self.async_update_reload_and_abort(
                manager, data=with_arcane(manager, arcane), reason="arcane_updated"
            )
        title = "Arcane"
        env_id = "0"
        machine: dict[str, Any] = {}
        try:
            client = ArcaneClient(async_get_clientsession(self.hass), arcane["url"], arcane["api_key"])
            snapshot = await client.snapshot(include_remote=bool(arcane.get("include_remote", True)))
        except ArcaneError:
            snapshot = {}
        for item in snapshot.get("machines") or []:
            if str(item.get("env_id") or "") == "0":
                machine = item
                break
        if machine:
            title = str(machine.get("title") or title)
            env_id = str(machine.get("env_id") or env_id)
            found = find_entry_for_machine(self.hass, machine)
            if found is not None:
                data = entry_payload({**dict(found.data), CONF_ARCANE: arcane, CONF_ARCANE_ENV: env_id}, entry_nodes(found.data))
                return self.async_update_reload_and_abort(found, data=data, reason="arcane_updated")
        await self.async_set_unique_id(f"arcane:{env_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=title,
            data=entry_payload(
                {
                    CONF_ARCANE: arcane,
                    CONF_ARCANE_ENV: env_id,
                    CONF_AUTO_TITLE: title,
                    CONF_MACHINE_NAME: machine.get("name") or "",
                    CONF_MACHINE_IP: machine.get("ip") or "",
                },
                [],
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RavnOptionsFlow:
        """Configure the Arcane manager on an existing entry."""
        return RavnOptionsFlow()

    def _abort_known(self, node_id: str) -> ConfigFlowResult | None:
        """Update the address of a node that already has an entry."""
        entry = entry_for_node(self.hass, node_id)
        if entry is None:
            return None
        current = find_node(entry, node_id)
        if current is None:
            return None
        host = self._host or current["host"]
        port = int(self._port)
        if current["host"] != host or int(current["port"]) != port:
            self.hass.config_entries.async_update_entry(
                entry,
                data=with_node(entry, {**current, "host": host, "port": port}),
            )
            if entry.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_RETRY):
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason="already_configured")

    async def _async_store_node(self, node: dict[str, Any]) -> ConfigFlowResult:
        """Create one entry for this machine, or attach the agent to the Arcane entry."""
        node = node_dict(node)
        existing = entry_for_node(self.hass, node[CONF_NODE_ID])
        if existing is not None:
            return self.async_update_reload_and_abort(
                existing, data=with_node(existing, node), reason="already_configured"
            )
        host = str(node["host"]).strip().lower()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if host and host in machine_keys(entry) and find_node(entry, node[CONF_NODE_ID]) is None:
                self.hass.config_entries.async_update_entry(entry, data=with_node(entry, node))
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="node_added")
        await self.async_set_unique_id(node[CONF_NODE_ID])
        self._abort_if_unique_id_configured()
        title = self._name or node["host"]
        return self.async_create_entry(
            title=title,
            data=entry_payload({CONF_MACHINE_IP: node["host"], CONF_AUTO_TITLE: title}, [node]),
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Update the agent connection stored on this machine."""
        entry = self._get_reconfigure_entry()
        nodes = entry_nodes(entry.data)
        if not nodes:
            return self.async_abort(reason="no_nodes")
        if len(nodes) == 1:
            self._node_id = nodes[0][CONF_NODE_ID]
            return await self.async_step_reconfigure_node()
        return await self.async_step_reconfigure_pick()

    async def async_step_reconfigure_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which machine to update."""
        entry = self._get_reconfigure_entry()
        nodes = entry_nodes(entry.data)
        if user_input is not None:
            self._node_id = str(user_input[CONF_NODE_ID])
            return await self.async_step_reconfigure_node()
        options = [
            {"label": f"{node['host']}:{node['port']}", "value": node[CONF_NODE_ID]} for node in nodes
        ]
        return self.async_show_form(
            step_id="reconfigure_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NODE_ID): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
        )

    async def async_step_reconfigure_node(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace the host, port, or token for one node."""
        entry = self._get_reconfigure_entry()
        current = find_node(entry, self._node_id or "")
        if current is None:
            return self.async_abort(reason="no_nodes")
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            token = user_input[CONF_TOKEN].strip()
            try:
                fingerprint = await probe_fingerprint(host, port)
                client = RavnClient(async_get_clientsession(self.hass), host, port, token, fingerprint)
                identity = await client.identity()
            except RavnAuthError:
                errors["base"] = "invalid_auth"
            except RavnError:
                errors["base"] = "cannot_connect"
            else:
                if str(identity.get("node_id")) != current[CONF_NODE_ID]:
                    errors["base"] = "wrong_node"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data=with_node(
                            entry,
                            {
                                CONF_NODE_ID: current[CONF_NODE_ID],
                                CONF_HOST: host,
                                CONF_PORT: port,
                                CONF_TOKEN: token,
                                CONF_FINGERPRINT: fingerprint,
                            },
                        ),
                    )
        return self.async_show_form(
            step_id="reconfigure_node",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=current["host"]): str,
                    vol.Required(CONF_PORT, default=current["port"]): int,
                    vol.Required(CONF_TOKEN): str,
                }
            ),
            errors=errors,
        )


class RavnOptionsFlow(OptionsFlow):
    """Update the Arcane manager without changing paired nodes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the manager URL and API key. Remove disconnects Arcane."""
        entry = manager_entry(self.hass) or self.config_entry
        existing = entry.data.get(CONF_ARCANE)
        if not isinstance(existing, dict):
            existing = None
        errors: dict[str, str] = {}
        if user_input is not None and user_input.get("remove"):
            self.hass.config_entries.async_update_entry(entry, data=with_arcane(entry, None))
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(title="", data={})
        if user_input is not None:
            error, arcane = await _validate_arcane(self.hass, user_input, existing, allow_clear=False)
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(entry, data=with_arcane(entry, arcane))
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="init",
            data_schema=_arcane_schema(existing or {}, key_required=False, allow_clear=True),
            errors=errors,
        )
