"""Pydantic models for NCS configuration.

This module defines type-safe configuration schemas using Pydantic v2.
The configuration is hierarchical and supports layered overrides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="INFO", description="Minimum log level")
    file: Path | None = Field(default=None, description="Log file path")
    rotation: str = Field(default="10 MB", description="Log rotation size/time")
    retention: str = Field(default="1 week", description="Log retention period")
    console: bool = Field(default=True, description="Enable console logging")
    colorize: bool = Field(default=True, description="Enable colored output")


class AuthProviderConfig(BaseModel):
    """Authentication provider configuration."""

    type: Literal["local", "keycloak"] = Field(
        default="local", description="Auth provider type"
    )
    # Keycloak-specific settings
    server_url: str = Field(default="", description="Keycloak server URL")
    realm: str = Field(default="", description="Keycloak realm")
    client_id: str = Field(default="ncs", description="OIDC client ID")
    client_secret: str = Field(default="", description="OIDC client secret (optional)")
    redirect_uri: str = Field(
        default="http://localhost:8089/callback",
        description="OAuth callback URI",
    )


class AuthConfig(BaseModel):
    """Authentication and authorization configuration."""

    provider: AuthProviderConfig = Field(default_factory=AuthProviderConfig)
    session_timeout_minutes: int = Field(
        default=480, ge=5, le=1440, description="Session timeout in minutes"
    )
    auto_login: bool = Field(
        default=False, description="Attempt automatic login on startup"
    )
    remember_user: bool = Field(
        default=True, description="Remember last logged in user"
    )
    offline_mode_enabled: bool = Field(
        default=True, description="Allow offline mode when auth unavailable"
    )


class PreferencesConfig(BaseModel):
    """User preferences stored in config."""

    theme: str = Field(default="system", description="Theme: light, dark, system")
    font_size: int = Field(default=10, ge=6, le=24, description="Base font size")
    font_family: str = Field(default="", description="Font family (empty = system)")
    show_statusbar: bool = Field(default=True, description="Show status bar")
    show_toolbar: bool = Field(default=True, description="Show toolbar")
    recent_files: list[str] = Field(default_factory=list, description="Recent files")
    recent_files_limit: int = Field(default=10, ge=0, le=50, description="Max recent files")

    # Beamline-specific overrides can be stored under beamlines.{name}.*
    beamlines: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Per-beamline preference overrides"
    )


class UIConfig(BaseModel):
    """User interface configuration."""

    theme: str = Field(default="system", description="Theme name: light, dark, system")
    font_family: str = Field(default="", description="UI font family (empty = system default)")
    font_size: int = Field(default=10, ge=6, le=24, description="Base font size in points")
    show_statusbar: bool = Field(default=True, description="Show status bar")
    show_toolbar: bool = Field(default=True, description="Show toolbar")
    remember_geometry: bool = Field(default=True, description="Remember window position/size")
    recent_files_limit: int = Field(default=10, ge=0, le=50, description="Max recent files")


class AcquisitionConfig(BaseModel):
    """Data acquisition configuration."""

    default_timeout: float = Field(
        default=30.0, ge=1.0, description="Default operation timeout in seconds"
    )
    auto_save: bool = Field(default=True, description="Automatically save acquired data")
    data_directory: Path = Field(
        default=Path("~/ncs_data").expanduser(),
        description="Default data storage directory",
    )
    file_format: str = Field(
        default="hdf5", description="Default file format: hdf5, nexus, csv"
    )
    compression: str = Field(
        default="gzip", description="Compression method: none, gzip, lz4"
    )


class BeamlineConfig(BaseModel):
    """Beamline-specific configuration."""

    name: str = Field(default="", description="Beamline identifier")
    description: str = Field(default="", description="Beamline description")
    sector: str = Field(default="", description="Sector/location")
    epics_prefix: str = Field(default="", description="EPICS PV prefix for this beamline")
    custom: dict[str, Any] = Field(
        default_factory=dict, description="Beamline-specific custom settings"
    )


class NCSConfig(BaseModel):
    """Root configuration model for NCS.

    This is the top-level configuration that aggregates all sub-configurations.
    """

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    preferences: PreferencesConfig = Field(default_factory=PreferencesConfig)
    acquisition: AcquisitionConfig = Field(default_factory=AcquisitionConfig)
    beamline: BeamlineConfig = Field(default_factory=BeamlineConfig)

    # Extensible settings for plugins
    extensions: dict[str, Any] = Field(
        default_factory=dict,
        description="Extension/plugin configuration namespace",
    )

    model_config = {
        "extra": "allow",  # Allow extra fields for forward compatibility
        "validate_default": True,
    }
