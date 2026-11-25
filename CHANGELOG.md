# Change history

Releases are listed newest first. The integration and the agent are listed only when that part changes. 26.10.27 is the first release.

## 2026.48.1

Integration and agent. Versions are now `YYYY.W.R`, from the publication time in Europe/Berlin. Each stream has its own counter. Already published versions stay as they are.

## 26.11.24

Integration.

- Arcane 3 renamed `/api/environments` to `/api/agents`. Container lists, start, stop, restart, redeploy, and image updates use the new routes. Start, stop, and restart are sent as `?action=`.

## 26.10.28

Integration.

- Each container has **Custom URL** and **Custom name** under Configuration. Both start empty. The same strings are attributes `custom_url` and `custom_name` on that container's other entities.
- **Exclude container**, also under Configuration, leaves the container in Home Assistant and lets a dashboard skip it.
- **Hide missing containers after** and **Show updates in Home Assistant** stay saved. Set the hours to 0 to keep a vanished container until **Clean up missing**.

Agent.

- Agent builds are published from [pixelwave/ravn-agent](https://github.com/pixelwave/ravn-agent). An integration release no longer looks like an agent update.

The Home Assistant tile layout is described in [HA-DASHBOARD.md](HA-DASHBOARD.md).

## 26.10.27

First release. The features are described in the [README](README.md).
