# Using Panels

Lightfall uses a flexible dockable panel system. Panels can be rearranged, tabbed together, or floated as separate windows. This guide describes each panel and how to use it.

## Managing Panels

### Opening Panels

To open a panel:
- Go to **View** > **Panels** and select the panel
- Or use the toolbar quick-access buttons (if configured)

### Rearranging Panels

Panels can be customized to fit your workflow:

- **Move**: Drag a panel's title bar to reposition it
- **Tab**: Drop a panel onto another to create tabs
- **Float**: Drag a panel outside the main window
- **Resize**: Drag panel borders to adjust size

### Saving Layout

Your panel layout is saved automatically when you close Lightfall. To explicitly save or restore layouts:

- **File** > **Save Layout** - Save current arrangement
- **File** > **Restore Default Layout** - Reset to default

## Available Panels

### Bluesky Panel

**Purpose**: Select and execute data acquisition plans

**Features**:
- Plan browser with search and categories
- Dynamic parameter configuration
- Run/Pause/Abort controls
- User plan creation and management

**Typical Use**: Primary interface for running experiments. Select a plan, configure parameters, and execute.

See [Running Plans](running-plans.md) for detailed usage.

---

### Devices Panel

**Purpose**: Browse and control hardware devices

**Features**:
- Tree view of all devices organized by category
- Search and filter by name or type
- Device details: current value, status, metadata
- Control widgets for adjustable devices
- Context menu for device actions

**Typical Use**: Monitor hardware status, check motor positions, adjust device parameters.

**Device Information Shown**:
- Name and type
- Current value and units
- Connection status
- Component hierarchy (for complex devices)
- Category and tags

**Controlling Devices**:
1. Select a device in the tree
2. The details pane shows current state
3. For controllable devices, a control widget appears
4. Enter new values and apply

---

### Logbook Panel

**Purpose**: Document experiments automatically and manually

**Features**:
- Automatic entries from plans and device changes
- User notes with rich text editing
- Protected system entries (cannot be accidentally modified)
- Active project/logbook header
- Export capabilities

**Typical Use**: Review experiment history, add notes about observations, document procedures.

**Automatic Entries**:
- Plan start with parameters
- Plan completion or abort
- Device state changes
- System events

**Adding Notes**:
1. Click **Add Note** in the toolbar
2. Enter your observation or comment
3. The note is timestamped and saved

**Entry Protection**:
System-generated entries are protected from editing. User notes can be edited freely. The protection system prevents accidental data loss.

---

### Claude Assistant Panel

**Purpose**: AI-powered help and natural language control

**Features**:
- Chat interface with Claude AI
- Understands Lightfall context and capabilities
- Can control panels and devices via MCP tools
- Provides help and explanations
- Executes multi-step procedures

**Typical Use**: Get help with procedures, control the application via natural language, automate repetitive tasks.

See [Claude Assistant](claude-assistant.md) for detailed usage.

---

### Documents Panel

**Purpose**: View raw Bluesky event documents

**Features**:
- Real-time document streaming during acquisition
- Document type filtering
- Detailed view of document contents

**Typical Use**: Debug data acquisition issues, understand document flow, inspect raw metadata.

---

### IPython Panel

**Purpose**: Interactive Python console

**Features**:
- Full IPython REPL
- Direct access to Lightfall objects (`main_window`, `app`)
- Widget targeting mode (select UI elements for inspection)
- Command history

**Typical Use**: Advanced scripting, debugging, automation development, one-off calculations.

**Pre-loaded Objects**:
```python
main_window  # Main application window
app          # LFApplication instance
```

**Widget Targeting**:
1. Enable crosshair mode in the toolbar
2. Click any UI element
3. A reference appears in the console for inspection

---

### Logging Panel

**Purpose**: View application logs

**Features**:
- Real-time log display
- Log level filtering (DEBUG, INFO, WARNING, ERROR)
- Search and filter capabilities
- Copy and export logs

**Typical Use**: Diagnose issues, monitor system health, debug problems.

---

### Tiled Browser Panel

**Purpose**: Browse data stored in Tiled catalog

**Features**:
- Query data by date range
- Filter by plan type or exit status
- Pagination for large datasets
- Data preview

**Typical Use**: Find previous experiment data, review historical results, locate specific acquisitions.

**Requires**: Tiled server configuration in Preferences.

---

### Threads Panel

**Purpose**: Monitor application threads and async tasks

**Features**:
- List of active threads
- Thread state and stack information
- Task status monitoring

**Typical Use**: Advanced debugging of threading issues or hung operations.

## Panel Reference

| Panel | Category | Default Position | Closable |
|-------|----------|------------------|----------|
| Bluesky | Acquisition | Left (tabbed) | Yes |
| Devices | Hardware | Left (tabbed) | Yes |
| Logbook | Documentation | Right | Yes |
| Claude | Tools | Left (tabbed) | Yes |
| Documents | Data | - | Yes |
| IPython | Scripting | - | Yes |
| Logging | System | - | Yes |
| Tiled Browser | Data | - | Yes |
| Threads | Advanced | - | Yes |
