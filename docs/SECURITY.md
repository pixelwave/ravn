# Security

RAVN reads metrics. The running agent does not change the host system, install packages, edit other services, or run commands from a request.

## What the agent writes

These are the only writes, and they stay inside RAVN's own files:

| Path | What | When |
| --- | --- | --- |
| `/etc/ravn` | Node id, token, TLS certificate, sensor on/off settings | First start, and when a sensor setting changes |
| `/var/lib/ravn` | A staged update binary and temporary downloads | Only during an update. Partial files are deleted. The installer deletes the staged file. |
| `/usr/local/bin/ravn` | The agent binary | `ravn-apply`, the root helper, after a checksum-verified update |
| systemd units and `/usr/local/libexec/ravn-apply` | Service files | `install.sh` and `uninstall.sh` only |

`ravn-update.path` watches `/var/lib/ravn`. When a staged binary owned by the `ravn` user appears, `ravn-apply` installs that one file, deletes the staging files, and restarts the agent. It refuses symlinks and files owned by anyone else.

The agent does not use `sudo`. An update URL cannot be chosen in the request. The release repository is the one stored in `/etc/ravn/config.json`, and the download must match `SHA256SUMS`.

Sensor collection reads `/proc`, `/sys`, and `/etc/hostname`. It does not write those paths.

## Ask first

Any change that needs a write outside the table above has to be asked for before it is implemented, and then documented here.

## Network

The API listens on the LAN with HTTPS. Clients pin the certificate fingerprint. Every route needs the bearer token. mDNS advertises the node id, version, and fingerprint. It does not advertise the token.

The installer and `ravn status` print the token. It is stored in `/etc/ravn/token` mode `0600`. Do not commit it, and do not put it in a dashboard.

## Updates and leftover files

An update deletes partial downloads. A successful install deletes the staged binary. Uninstall deletes the program, the helper, the units, and `/var/lib/ravn`. `--purge` also deletes `/etc/ravn` and the `ravn` user. Old sensor ids are dropped from `config.json` when the file is loaded, so a removed sensor does not stay in the file.
