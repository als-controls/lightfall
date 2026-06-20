"""Pure-logic accumulator for the Claude panel title-bar cockpit.

Holds the running per-session cost, token totals, and latest context-window
usage, and formats them into a compact title-bar string. No Qt here so it is
unit-testable in isolation: the panel owns one instance, feeds it agent-signal
payloads, and pushes ``format()`` into the title-bar label.
"""
from __future__ import annotations

from dataclasses import dataclass

_SEP = "  ·  "  # middle dot with padding


@dataclass
class CockpitState:
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    context_pct: float | None = None  # 0..100; None until the first reading
    auto_compact: bool = False

    def add_result(self, info: dict) -> None:
        """Accumulate one per-turn ResultMessage payload (cost is per-turn)."""
        self.cost_usd += float(info.get("total_cost_usd") or 0.0)
        self.input_tokens += int(info.get("input_tokens") or 0)
        self.output_tokens += int(info.get("output_tokens") or 0)

    def set_context(self, info: dict) -> None:
        """Record the latest context-window usage reading."""
        total = info.get("totalTokens")
        max_tokens = info.get("maxTokens")
        if total is not None and max_tokens:
            self.context_pct = 100.0 * float(total) / float(max_tokens)
        else:
            pct = info.get("percentage")
            if pct is not None:
                # SDK may report 0..1 or 0..100; normalize to 0..100.
                self.context_pct = float(pct) * 100.0 if float(pct) <= 1.0 else float(pct)
        self.auto_compact = bool(info.get("isAutoCompactEnabled", False))

    def reset(self) -> None:
        self.cost_usd = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.context_pct = None
        self.auto_compact = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def format(self) -> str:
        """Compact title-bar string; omits segments without data."""
        segments = [f"${self.cost_usd:.4f}"]
        if self.context_pct is not None:
            segments.append(f"{self.context_pct:.0f}% ctx")
        if self.total_tokens:
            segments.append(f"{self.total_tokens / 1000:.1f}k tok")
        return _SEP.join(segments)

    def tooltip(self) -> str:
        lines = [
            f"Session cost: ${self.cost_usd:.4f}",
            f"Tokens: {self.input_tokens:,} in / {self.output_tokens:,} out",
        ]
        if self.context_pct is not None:
            lines.append(f"Context window: {self.context_pct:.1f}% used")
            lines.append(f"Auto-compact: {'on' if self.auto_compact else 'off'}")
        return "\n".join(lines)
