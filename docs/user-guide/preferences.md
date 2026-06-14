# Preferences

All settings live in the Settings dialog: **File → Settings...** or
`Ctrl+,`. Pages are listed on the left; most changes apply when you confirm,
and the few that need an application restart (device backends, plugin
enable/disable) say so on their page.

> 🖼️ **Image placeholder** — *Screenshot: Settings dialog with the page list on the left and the Appearance page selected*

## Appearance

- **Theme** — pick from the built-in themes (light and several dark
  variants); theme plugins can add more. Changes preview immediately.
- **Font size** — interface font, 8–24 pt.

## User Profile

Set a profile picture and review your identity. The avatar appears in the
top-right corner of the main window as a visual indicator of who is signed
in.

## Network Proxy

Proxy configuration for outbound connections (e.g. reaching facility
services through a SOCKS proxy from offsite). Applies to the embedded
browser and HTTP clients.

## Login & Session

- **Session duration** — how long a local-account session lasts before
  re-authentication: 15 minutes to 8 hours, default 2 hours. Keycloak
  session lifetimes are controlled by the Keycloak server, not this setting.

## External Tools

Selects the code editor Lightfall opens for user plans and plugin files:

- **VSCode** — uses the `vscode://` URL protocol (registered automatically
  by VSCode)
- **PyCharm** — uses the `jetbrains://` protocol, which requires JetBrains
  Toolbox

The page shows the exact protocol URLs and warns if the handler is not
detected.

## Devices

Configures the device backends. **Multiple backends can be enabled at once**;
their devices merge into a single catalog. Changes take effect on restart.

| Backend | Use |
|---------|-----|
| **Mock** | Simulated devices (ophyd.sim) for development and training — enabled by default. Option to include the noisy detector. |
| **BCS** | Real hardware via the Beamline Control System (host, port, timeout, beamline). |
| **Happi** | Devices from a [happi](https://pcdshub.github.io/happi/) database (path, beamline filter, instantiation mode). |

The page also holds connection settings (device instantiation mode,
connection timeout, connect-on-startup) and **Remote Access (CA Tunnel)** for
reaching EPICS Channel Access across networks.

## Tiled Data Catalog

Connection to the Tiled data server that backs the Data Browser and
Visualization panels and receives acquired runs:

- **Enable** toggle and **server URL**
- **Authentication mode** — none, API key, or Keycloak (reuses your
  Lightfall login)

## Logbook

- **Enable logbook backend sync** and the **server URL**, with a connection
  test
- **Force offline-only mode** — local SQLite only, no sync (see
  [Using the Logbook](logbook.md))

## IPC

NATS message-broker connection for inter-process features (e.g. Tsuchinoko
adaptive experiments, cross-instance notifications): server URL, topic
prefix, display name, with a connection status indicator.

## Claude Assistant

API endpoint and credentials (API key or Claude Code OAuth), model
selection, max turns, and the permission mode. Covered in
[Claude Assistant](claude-assistant.md).

## Assistant Tools

Enable or disable individual agent plugins — the skill prompts and tool
bundles available to the assistant.

## Plugins

Lists discovered plugins with per-plugin enable/disable and status. Plugin
changes take effect on restart. Some plugins contribute their own settings
pages, which appear in this dialog alongside the built-in ones.

## Visualization

Defaults for the Visualization panel: automatic visualization-type selection
(or a fixed default type), performance limits (decimation threshold, update
rate), and plot appearance (default colormap, grid).

---

## Where settings are stored

Two mechanisms, both per-user:

1. **Qt state** (window geometry, dock layout, sidebar arrangement) — stored
   via QSettings in the platform-native location (registry on Windows,
   `~/.config` on Linux, plists on macOS).
2. **Typed preferences** (everything in the Settings dialog) — the layered
   config system, with user values in `%APPDATA%/ncs/` on Windows or
   `~/.config/ncs/` on Linux/macOS, over site-wide and packaged defaults.

A handful of preferences (such as device favorites) are additionally synced
to your user profile on the settings server when one is deployed, so they
follow you between workstations.

### Beamline overrides

Deployments can ship site configuration that pre-sets defaults per beamline
(device backend, theme, data directories). Your personal settings layer on
top of those defaults.

## Troubleshooting

### A change didn't take effect

Device backend and plugin changes require a restart; their pages say so.
Everything else applies on confirm — if a theme looks wrong after switching,
toggle to another theme and back.

### Resetting to defaults

Close Lightfall and delete the user config directory (`%APPDATA%/ncs/` or
`~/.config/ncs/`). This clears typed preferences; window layout resets if
you also clear the Qt state (on Windows: the `ALS/NCS` key under
`HKEY_CURRENT_USER\Software`).
