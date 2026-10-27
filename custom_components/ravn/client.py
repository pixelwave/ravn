"""HTTPS client for one RAVN agent.

The token is sent only after the TLS peer certificate matches the pinned fingerprint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from typing import Any

import aiohttp

from .const import DEFAULT_PORT


class RavnError(Exception):
    """Base error for agent calls."""


class RavnAuthError(RavnError):
    """The agent rejected the token."""


class RavnConnectError(RavnError):
    """The agent could not be reached."""


class RavnFingerprintError(RavnError):
    """The certificate did not match the pin."""


def normalize_fingerprint(value: str) -> str:
    """Return a lowercase hex SHA-256 fingerprint without separators."""
    return value.replace(":", "").strip().lower()


async def probe_fingerprint(host: str, port: int = DEFAULT_PORT) -> str:
    """Read the server certificate without sending a token."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context),
            timeout=10,
        )
    except (TimeoutError, OSError) as err:
        raise RavnConnectError("could not connect to the agent") from err
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise RavnConnectError("connection was not TLS")
        der = ssl_object.getpeercert(binary_form=True)
        if not der:
            raise RavnConnectError("agent did not present a certificate")
        return hashlib.sha256(der).hexdigest()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


class RavnClient:
    """Small client over the shared Home Assistant aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        token: str,
        fingerprint: str,
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._token = token
        self._ssl = aiohttp.Fingerprint(bytes.fromhex(normalize_fingerprint(fingerprint)))

    def _url(self, path: str) -> str:
        host = self._host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"https://{host}:{self._port}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                self._url(path),
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                text = await response.text()
                if response.status == 401:
                    raise RavnAuthError("token was rejected")
                if response.status >= 400:
                    raise RavnError(f"agent returned {response.status}")
                if not text:
                    return {}
                payload = json.loads(text)
        except RavnError:
            raise
        except (aiohttp.ClientError, TimeoutError, ssl.SSLError) as err:
            message = str(err).lower()
            if "fingerprint" in message:
                raise RavnFingerprintError("certificate fingerprint did not match") from err
            raise RavnConnectError("could not connect to the agent") from err
        if not isinstance(payload, dict):
            raise RavnError("agent returned an unexpected payload")
        return payload

    async def identity(self) -> dict[str, Any]:
        """Return node identity."""
        return await self._request("GET", "/v1/identity")

    async def readings(self) -> dict[str, Any]:
        """Return the cached snapshot, including the sensor catalog."""
        return await self._request("GET", "/v1/readings")

    async def put_sensor(self, sensor_id: str, enabled: bool, interval_s: int) -> dict[str, Any]:
        """Store a sensor toggle and interval on the node."""
        return await self._request(
            "PUT",
            f"/v1/sensors/{sensor_id}",
            {"enabled": enabled, "interval_s": interval_s},
        )

    async def update_check(self) -> dict[str, Any]:
        """Ask the agent to refresh its release lookup."""
        return await self._request("POST", "/v1/update/check", {}, timeout=30)

    async def install_update(self, *, force: bool = False) -> dict[str, Any]:
        """Ask the agent to install its configured release.

        The agent restarts itself when the install succeeds, which drops the connection.
        """
        try:
            return await self._request("POST", "/v1/update", {"force": force}, timeout=60)
        except RavnConnectError:
            return {"restarting": True}
