If you find my work useful, please consider supporting:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-pixelWAVE-ea4aaa?logo=github-sponsors&logoColor=white)](https://donate.p-wave.de/)

<p align="left">
  <img src="brand/logo.svg" alt="RAVN" width="25%">
</p>

# RAVN

In Norse myth, Odin's ravens flew out and came back with news.

* RAVN is a custom integration for Home Assistant. 
* RAVN connects with your arcane manager.
* RAVN comes with an optional lightweight node agent.

RAVN let's you observe the machines on your network.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/wordmark-day.svg">
    <img src="brand/wordmark-night.svg" alt="RAVN" width="100%">
  </picture>
</p>

The integration is the part that lives in Home Assistant. Each machine you add becomes its own entry, so the network is there for dashboards and automations.

The agent is optional. Install the small program on a Linux machine when you want that machine's sensors: its name, its addresses, and readings such as temperature, disk, CPU, memory, fans, and load. There is no monitoring stack to host, no MQTT broker, and no cloud account.

Docker comes in through Arcane. Connect the Arcane manager you already run, and Home Assistant shows its containers. From there you can start, stop, restart, and redeploy them.

<img width="2062" height="1432" alt="img-1" src="https://github.com/user-attachments/assets/b7231b89-a635-4257-8889-9f43d1714d07" />


<img width="1156" height="778" alt="img-2" src="https://github.com/user-attachments/assets/694998e0-1aad-4e05-bfcf-2ea3b8ab2c0b" />


<img width="2155" height="1070" alt="img-3" src="https://github.com/user-attachments/assets/8434c521-988b-4269-afb8-236382d255f1" />


<img width="1238" height="866" alt="img-5" src="https://github.com/user-attachments/assets/c6dbf3d5-357b-4df1-9108-2321b942d9f3" />


### Home Assistant

- One entry per machine, titled `hostname • address`, with an optional `ravn-agent` device if installed.
- Name and IP are on. Temperature, disk, CPU, memory, fans, and load stay off until enabled.
- Entity ids start with `ravn_`.
- If set up one Arcane manager lists each container, with Running, Restart, and Redeploy.

### Agent

- Linux agent with HTTPS and mDNS. The token is in `/etc/ravn/token`.
- `ravn status` prints the node panel and the token.
- Updates are checksum-verified. Installing again keeps the node id, token, and certificate.

# Deployment

## Home Assistant Integration

1. In HACS, add `https://github.com/pixelwave/ravn` as a custom integration repository and install RAVN.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → RAVN**.
4. Confirm the discovered node, check that the fingerprint matches the installer output, and paste the token.

If the node cannot advertise, enter the host and port yourself.

From **Add integration → RAVN**, choose **Arcane manager** to connect Docker through one Arcane manager ([getarcane.app](https://getarcane.app)). Open **Configure** on a machine later to change that connection.

## Node Agent - Install

```bash
curl -fsSL https://pixelwave.github.io/ravn/install.sh | bash
```

The script prints a URL, a token, and a certificate fingerprint. You need those in Home Assistant.
The agent checks for a newer release and stages it. Home Assistant shows that on the Agent row and can start the install.

### Remove agent on a node

```bash
curl -fsSL https://pixelwave.github.io/ravn/uninstall.sh | bash
```

To delete `/etc/ravn` as well:

```bash
curl -fsSL https://pixelwave.github.io/ravn/uninstall.sh | bash -s -- --purge
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

