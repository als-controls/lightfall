"""Filter predicates for trigger matching.

Phase 1 fixed-set: plan_name, tags_includes, start_doc_match. Combinations
are conjunctive (AND). Free-form expressions (jq-style) are deferred.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FilterPredicate:
    """Predicate evaluated against a bluesky start doc."""

    plan_name: str | list[str] | None = None
    tags_includes: str | list[str] | None = None
    start_doc_match: dict[str, Any] | None = None

    def matches(self, start_doc: dict[str, Any]) -> bool:
        if self.plan_name is not None:
            allowed = [self.plan_name] if isinstance(self.plan_name, str) else list(self.plan_name)
            if start_doc.get("plan_name") not in allowed:
                return False

        if self.tags_includes is not None:
            wanted = {self.tags_includes} if isinstance(self.tags_includes, str) else set(self.tags_includes)
            doc_tags = set(start_doc.get("tags") or [])
            if not wanted & doc_tags:
                return False

        if self.start_doc_match:
            for k, v in self.start_doc_match.items():
                if start_doc.get(k) != v:
                    return False

        return True
