# Change history

Releases are listed newest first. The integration and the agent are listed only when that part changes. 26.10.27 is the first release.

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
