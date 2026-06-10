# ThemePlugin

Theme plugins define application color schemes.

## Purpose

Use `ThemePlugin` when you want to:
- Add a custom color theme
- Create beamline-branded appearance
- Provide dark/light mode variants

## Base Class

```python
from lightfall.plugins.theme_plugin import ThemePlugin, ThemeDefinition
```

## Class Attributes

| Attribute | Value | Description |
|-----------|-------|-------------|
| `type_name` | `"theme"` | Plugin type identifier |
| `is_singleton` | `True` | One instance per plugin |

## Required Methods

### name (property)

Unique identifier for this theme.

```python
@property
def name(self) -> str:
    return "my_theme"
```

### display_name (property)

Human-readable name shown in the theme selector.

```python
@property
def display_name(self) -> str:
    return "My Theme"
```

### is_dark (property)

Whether this is a dark theme.

```python
@property
def is_dark(self) -> bool:
    return True  # or False for light themes
```

### get_theme_definition()

Return the theme's color definitions.

```python
def get_theme_definition(self) -> ThemeDefinition:
    return ThemeDefinition(
        primary="#3b82f6",
        background="#1a1a1a",
        # ... other colors
    )
```

## ThemeDefinition

The `ThemeDefinition` dataclass defines all theme colors:

```python
@dataclass
class ThemeDefinition:
    # Accent colors
    primary: str = "#2563eb"      # Primary brand/accent
    secondary: str = "#7c3aed"    # Secondary accent
    success: str = "#16a34a"      # Success/positive state
    warning: str = "#d97706"      # Warning state
    error: str = "#dc2626"        # Error/danger state
    info: str = "#0891b2"         # Informational state

    # Surface colors
    background: str = "#ffffff"   # Main background
    surface: str = "#f3f4f6"      # Elevated surface
    text: str = "#1f2937"         # Primary text
    text_secondary: str = "#6b7280"  # Secondary text
    border: str = "#e5e7eb"       # Borders/dividers

    # State colors (default to success/error)
    connected: str = ""           # Connected state
    disconnected: str = ""        # Disconnected state

    # Optional CSS
    css_overrides: str = ""       # Additional CSS rules
```

## Lifecycle

1. Theme plugins are loaded with `preload=True`
2. `get_theme_definition()` provides color values
3. Theme is registered with `ThemeRegistry`
4. Users select themes in Appearance settings
5. `ThemeManager` applies the selected theme

## Complete Example

### Dark Theme

```python
"""Custom dark theme plugin."""

from lightfall.plugins.theme_plugin import ThemePlugin, ThemeDefinition


class BeamlineDarkTheme(ThemePlugin):
    """Dark theme with beamline branding."""

    @property
    def name(self) -> str:
        return "beamline_dark"

    @property
    def display_name(self) -> str:
        return "Beamline Dark"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            # Brand colors
            primary="#00a0e4",       # Beamline blue
            secondary="#7c3aed",     # Purple accent

            # Status colors
            success="#22c55e",       # Green
            warning="#f59e0b",       # Amber
            error="#ef4444",         # Red
            info="#06b6d4",          # Cyan

            # Dark surface colors
            background="#0f172a",    # Very dark blue
            surface="#1e293b",       # Dark slate
            text="#f1f5f9",          # Light text
            text_secondary="#94a3b8", # Muted text
            border="#334155",        # Subtle border

            # State colors
            connected="#22c55e",
            disconnected="#ef4444",
        )
```

### Light Theme

```python
"""Custom light theme plugin."""

from lightfall.plugins.theme_plugin import ThemePlugin, ThemeDefinition


class BeamlineLightTheme(ThemePlugin):
    """Light theme with beamline branding."""

    @property
    def name(self) -> str:
        return "beamline_light"

    @property
    def display_name(self) -> str:
        return "Beamline Light"

    @property
    def is_dark(self) -> bool:
        return False

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            # Brand colors
            primary="#0077b6",       # Beamline blue
            secondary="#6366f1",     # Indigo accent

            # Status colors
            success="#16a34a",
            warning="#d97706",
            error="#dc2626",
            info="#0891b2",

            # Light surface colors
            background="#ffffff",
            surface="#f8fafc",
            text="#1e293b",
            text_secondary="#64748b",
            border="#e2e8f0",
        )
```

### Theme with CSS Overrides

```python
"""Theme with custom CSS overrides."""

from lightfall.plugins.theme_plugin import ThemePlugin, ThemeDefinition


class CustomStyledTheme(ThemePlugin):
    """Theme with additional CSS customizations."""

    @property
    def name(self) -> str:
        return "custom_styled"

    @property
    def display_name(self) -> str:
        return "Custom Styled"

    @property
    def is_dark(self) -> bool:
        return True

    def get_theme_definition(self) -> ThemeDefinition:
        return ThemeDefinition(
            primary="#8b5cf6",
            background="#18181b",
            surface="#27272a",
            text="#fafafa",
            text_secondary="#a1a1aa",
            border="#3f3f46",

            css_overrides="""
            /* Custom button styling */
            QPushButton {
                border-radius: 8px;
                padding: 8px 16px;
            }

            QPushButton:hover {
                background-color: #8b5cf6;
            }

            /* Custom scrollbar */
            QScrollBar:vertical {
                width: 8px;
                background: transparent;
            }

            QScrollBar::handle:vertical {
                background: #52525b;
                border-radius: 4px;
            }
            """
        )
```

## Registration

### Built-in Manifest

Themes must be preloaded to apply before the main window:

```python
PluginEntry(
    type_name="theme",
    name="beamline_dark",
    import_path="my_package.themes:BeamlineDarkTheme",
    preload=True,  # Required for themes
),
```

## Built-in Themes

Lightfall includes these themes by default:

| Theme | Type | Description |
|-------|------|-------------|
| `light` | Light | Clean light theme |
| `slate` | Dark | Modern slate gray |
| `darkblue` | Dark | Deep blue tones |
| `islands` | Dark | High contrast dark |

## System Theme Support

When `is_dark` is properly set, themes support system theme detection. Users can select "System" to automatically use a light or dark theme based on OS settings.

## Color Guidelines

### Accessibility

- Ensure sufficient contrast between text and background
- Use distinct colors for different states
- Test with color blindness simulators

### Consistency

- Use semantic colors consistently (success = green, error = red)
- Maintain visual hierarchy with surface colors
- Keep accent colors visible but not overwhelming

### Example Color Contrast

For dark themes:
- Background: #1a1a1a (very dark)
- Surface: #2a2a2a (slightly lighter)
- Text: #e0e0e0 (high contrast)
- Text secondary: #a0a0a0 (lower contrast)

For light themes:
- Background: #ffffff (white)
- Surface: #f5f5f5 (slightly darker)
- Text: #1a1a1a (high contrast)
- Text secondary: #666666 (lower contrast)
