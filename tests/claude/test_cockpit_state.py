from lightfall.claude.cockpit import CockpitState


def test_add_result_accumulates_across_turns():
    s = CockpitState()
    s.add_result({"total_cost_usd": 0.01, "input_tokens": 100, "output_tokens": 50})
    s.add_result({"total_cost_usd": 0.03, "input_tokens": 200, "output_tokens": 70})
    assert round(s.cost_usd, 4) == 0.04
    assert s.input_tokens == 300
    assert s.output_tokens == 120
    assert s.total_tokens == 420


def test_add_result_tolerates_missing_and_none():
    s = CockpitState()
    s.add_result({})
    s.add_result({"total_cost_usd": None, "input_tokens": None, "output_tokens": None})
    assert s.cost_usd == 0.0
    assert s.total_tokens == 0


def test_set_context_prefers_token_ratio():
    s = CockpitState()
    s.set_context({"totalTokens": 38000, "maxTokens": 100000, "isAutoCompactEnabled": True})
    assert round(s.context_pct, 1) == 38.0
    assert s.auto_compact is True


def test_set_context_normalizes_fractional_percentage():
    s = CockpitState()
    s.set_context({"percentage": 0.42})
    assert round(s.context_pct, 1) == 42.0


def test_format_omits_absent_segments():
    s = CockpitState()
    assert s.format() == "$0.0000"
    s.add_result({"total_cost_usd": 0.0421, "input_tokens": 12400, "output_tokens": 0})
    assert s.format() == "$0.0421  ·  12.4k tok"
    s.set_context({"totalTokens": 38000, "maxTokens": 100000})
    assert s.format() == "$0.0421  ·  38% ctx  ·  12.4k tok"


def test_reset_clears_everything():
    s = CockpitState()
    s.add_result({"total_cost_usd": 0.5, "input_tokens": 10, "output_tokens": 10})
    s.set_context({"percentage": 0.9})
    s.reset()
    assert s.format() == "$0.0000"
    assert s.context_pct is None
