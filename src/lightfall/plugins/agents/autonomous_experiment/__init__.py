"""Autonomous Experiment agent plugin.

Bridges Lightfall's embedded Claude agent to Tsuchinoko's NATS surface
for designing and running GP-driven adaptive experiments.

Spec: docs/superpowers/specs/2026-05-19-lightfall-autonomous-experiments-design.md
"""
from __future__ import annotations

from .plugin import AutonomousExperimentAgent

__all__ = ["AutonomousExperimentAgent"]
