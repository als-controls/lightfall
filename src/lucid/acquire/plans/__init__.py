"""Bluesky plan registry and management.

This module provides:
- PlanRegistry: Central registry of available Bluesky plans
- PlanInfo: Metadata about registered plans
- Default plan registration for standard bluesky.plans
- Utilities for plan display names and icons
- Custom NCS plans (scan_1d, rel_scan_1d)
- UserPlanService: Service for user-defined plans
"""

from lucid.acquire.plans.ncs_plans import rel_scan_1d, scan_1d
from lucid.acquire.plans.registry import (
    PLAN_CATEGORY_ICONS,
    ParameterInfo,
    PlanInfo,
    PlanRegistry,
    create_default_registry,
    get_registry,
    name_to_display_name,
)
from lucid.acquire.plans.user_plans import UserPlanService

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
    # User plans
    "UserPlanService",
]
