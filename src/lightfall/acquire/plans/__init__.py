"""Bluesky plan registry and management.

This module provides:
- PlanRegistry: Central registry of available Bluesky plans
- PlanInfo: Metadata about registered plans
- Default plan registration for standard bluesky.plans
- Utilities for plan display names and icons
- Custom NCS plans (scan_1d, rel_scan_1d)
- UserPlanService: Service for user-defined plans
"""

from lightfall.acquire.plans.lightfall_plans import rel_scan_1d, scan_1d
from lightfall.acquire.plans.registry import (
    PLAN_CATEGORY_ICONS,
    ParameterInfo,
    PlanInfo,
    PlanRegistry,
    create_default_registry,
    get_registry,
    name_to_display_name,
)
from lightfall.acquire.plans.stubs import (
    IMAGE_MODE_CONTINUOUS,
    IMAGE_MODE_MULTIPLE,
    IMAGE_MODE_SINGLE,
    set_image_mode,
    set_multiple_mode,
)
from lightfall.acquire.plans.user_plans import UserPlanService

__all__ = [
    # Registry
    "PLAN_CATEGORY_ICONS",
    "ParameterInfo",
    "PlanInfo",
    "PlanRegistry",
    "create_default_registry",
    "get_registry",
    "name_to_display_name",
    # NCS plans
    "scan_1d",
    "rel_scan_1d",
    # Shared plan stubs
    "set_image_mode",
    "set_multiple_mode",
    "IMAGE_MODE_SINGLE",
    "IMAGE_MODE_MULTIPLE",
    "IMAGE_MODE_CONTINUOUS",
    # User plans
    "UserPlanService",
]
