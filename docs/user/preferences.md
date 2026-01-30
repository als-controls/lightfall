# Preferences

LUCID provides extensive customization through the Preferences dialog. Access it via **Edit** > **Preferences** or `Ctrl+,`.

## Preference Categories

### Appearance

Customize the visual appearance of LUCID.

**Theme**: Select from available themes:
- **System**: Follow your operating system's light/dark setting
- **Light**: Light color scheme
- **Dark**: Dark color scheme
- Additional themes may be available from plugins

**Font Size**: Adjust the interface font size (8pt - 24pt)

Changes apply immediately for preview. Click **OK** to save or **Cancel** to revert.

---

### Device Backend

Configure how LUCID connects to hardware devices.

**Backend Selection**:

| Backend | Use Case |
|---------|----------|
| **Mock** | Development and testing without hardware |
| **BCS** | Real hardware via the Beamline Control System |

**Mock Backend Options**:
- Include/exclude noisy devices for realistic simulation

**BCS Backend Options**:
- **Host**: BCS server hostname
- **Port**: Connection port
- **Timeout**: Connection timeout
- **Beamline**: Select your beamline

**Note**: Backend changes take effect on next application start.

---

### Tiled Data Catalog

Configure data storage and retrieval.

**Enable Tiled**: Toggle Tiled integration on/off

**Server Configuration**:
- **URL**: Tiled server address
- **API Key**: For API key authentication

**Authentication Mode**:
- **None**: No authentication
- **API Key**: Use configured API key
- **Keycloak**: Use your LUCID login credentials

When using Keycloak authentication, your LUCID session credentials are automatically used for Tiled access.

---

### Claude Assistant

Configure the AI assistant.

**API Configuration**:
- **API Key**: Your Anthropic API key
- **Custom API URL**: For local deployments (leave blank for default)

**Model Settings**:
- **Model**: Select Claude model (e.g., claude-opus-4-5)
- **Max Turns**: Maximum conversation turns

**Skills**: Enable or disable available skills. Enabled skills add capabilities to Claude.

**Permission Mode**: Control how Claude interacts with the application
- **Restrictive**: Requires confirmation for most actions
- **Permissive**: Allows more autonomous operation

---

### Code Editor

Configure the external editor for user plans.

**Editor Selection**:
- **VSCode**: Visual Studio Code
- **PyCharm**: JetBrains PyCharm

**Protocol Handler**: LUCID uses URL protocols to open files in your editor. Ensure your editor's protocol handler is installed:

- VSCode: Usually automatic with installation
- PyCharm: Requires JetBrains Toolbox for the protocol handler

If the protocol handler is not detected, a warning appears with setup instructions.

---

### Login & Session

Configure authentication and session behavior.

**Session Duration**: How long before automatic logout (default: 8 hours)

**Session Timeout Warning**: When to show expiry warning (default: 15 minutes before)

---

### Plugins

Manage plugin loading and configuration.

**Available Plugins**: List of discovered plugins
- Enable/disable individual plugins
- View plugin information and status

**Plugin-Specific Settings**: Some plugins add their own preference pages

## Preference Storage

Preferences are stored in standard locations:

| Platform | Location |
|----------|----------|
| Windows | `%APPDATA%\lucid\` |
| macOS | `~/Library/Preferences/lucid/` |
| Linux | `~/.config/lucid/` |

Two types of data are stored:

1. **QSettings** (binary): Window geometry, dock positions
2. **Config files** (JSON/TOML): Typed preferences with validation

## Beamline-Specific Settings

Some settings can be overridden at the beamline level. When you connect to a beamline:

- Default device backend may be pre-configured
- Beamline-specific themes may be available
- Custom preferences may apply

These overrides ensure consistent configuration across all workstations at a beamline.

## Resetting Preferences

To reset all preferences to defaults:

1. Close LUCID
2. Delete the preference files from the storage location above
3. Restart LUCID

**Note**: This also resets window layouts and panel positions.

To reset only specific preferences:

1. Go to the relevant preference page
2. Change settings to desired values
3. Click **OK** to save

## Troubleshooting

### Changes don't take effect

Some settings require an application restart:
- Device backend selection
- Plugin enable/disable

The preference page indicates when a restart is required.

### Theme doesn't look right

If theme colors appear incorrect:
1. Try selecting a different theme, then switching back
2. Restart the application
3. Check if your system theme setting is interfering

### Can't connect to BCS backend

Verify:
1. BCS host and port are correct
2. Network connectivity to the BCS server
3. Required credentials are configured
4. Beamline selection is appropriate
