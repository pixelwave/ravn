If you find my work useful, please consider supporting:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-pixelWAVE-ea4aaa?logo=github-sponsors&logoColor=white)](https://donate.p-wave.de/)

<p align="left">
  <img src="brand/logo.svg" alt="RAVN" width="25%">
</p>

# RAVN

RAVN is a custom integration for Home Assistant. It is how you observe the machines on your local network. In Norse myth, Odin's ravens flew out and came back with news. These stay on your LAN.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/wordmark-day.svg">
    <img src="brand/wordmark-night.svg" alt="RAVN" width="100%">
  </picture>
</p>

The integration is the part that lives in Home Assistant. Each machine you add becomes its own entry, so the network is there for dashboards and automations.

The agent is optional. Install the small program on a Linux machine when you want that machine's sensors: its name, its addresses, and readings such as temperature, disk, CPU, memory, fans, and load. There is no monitoring stack to host, no MQTT broker, and no cloud account.

Docker comes in through Arcane. Connect the Arcane manager you already run, and Home Assistant shows its containers. From there you can start, stop, restart, and redeploy them.

## Versions

A release tag is the date it is published, `YY.MM.DD`. A version published today is `26.10.27`. A version published tomorrow is `26.10.28`.

A second release on the same day is `26.10.27.2`, then `26.10.27.3`, and so on. The first release of a day has three numbers.

The integration and the agent are versioned separately, so they can differ. The integration version is `custom_components/ravn/manifest.json`. The agent version is the git tag, stamped into the binary. Integration updates come from HACS. Agent updates come from the GitHub release of that tag.

## 26.10.27

First release.

### Home Assistant

- One entry per machine, titled `hostname • address`, with a `ravn-agent` device.
- Name and IP are on. Temperature, disk, CPU, memory, fans, and load stay off until enabled.
- Entity ids start with `ravn_`.
- One Arcane manager lists each container, with Running, Restart, and Redeploy.
- Container image updates stay off the updates page until **Show updates in Home Assistant** is on.
- An offline machine stays. A vanished container hides after 24 hours, or never when that interval is 0.

### Agent

- Linux agent with HTTPS and mDNS. The token is in `/etc/ravn/token`.
- `ravn status` prints the node panel and the token.
- Updates are checksum-verified. Installing again keeps the node id, token, and certificate.

## Install a node

```bash
curl -fsSL https://pixelwave.github.io/ravn/install.sh | bash
```

The script prints a URL, a token, and a certificate fingerprint. You need those in Home Assistant.

### Remove a node

```bash
curl -fsSL https://pixelwave.github.io/ravn/uninstall.sh | bash
```

To delete `/etc/ravn` as well:

```bash
curl -fsSL https://pixelwave.github.io/ravn/uninstall.sh | bash -s -- --purge
```

## Home Assistant

1. In HACS, add `https://github.com/pixelwave/ravn` as a custom integration repository and install RAVN.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → RAVN**.
4. Confirm the discovered node, check that the fingerprint matches the installer output, and paste the token.

If the node cannot advertise, enter the host and port yourself.

From **Add integration → RAVN**, choose **Arcane manager** to connect Docker through one Arcane manager ([getarcane.app](https://getarcane.app)). Open **Configure** on a machine later to change that connection.

## Updates

The agent checks for a newer release and stages it. Home Assistant shows that on the Agent row and can start the install.

```json
{
  "update": {
    "github_repo": "pixelwave/ravn"
  }
}
```

## Change history

Releases after 26.10.27 are listed here, newest first. The integration and the agent are listed only when that part changes.

## Development

The agent is a Go module in `agent/`. The integration is `custom_components/ravn/`.

```bash
cd agent && go test ./...
pip install -r requirements-test.txt && pytest
```

`agent/deploy/install.sh --binary ./ravn` installs a binary you built yourself:

```bash
cd agent && go build -ldflags "-X ravn/internal/version.Version=26.10.27" -o ravn ./cmd/ravn
```

## License

RAVN is free software under the GNU Affero General Public License,
version 3 only. See `LICENSE` and `COPYRIGHT`.

You can download it and run it for yourself. If you modify RAVN and let
other people use that modified version over a network, the AGPL requires
you to offer them the corresponding source.

Sensor readings, container state, and tokens are your data. The license
applies to the program, not to that data.

The corresponding source for an official release is the git tag that built it.

