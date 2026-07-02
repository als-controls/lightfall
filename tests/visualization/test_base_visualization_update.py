from typing import Any
from lightfall.visualization.base_visualization import BaseVisualization


class _StubViz(BaseVisualization):
    viz_name = "stub"
    viz_display_name = "Stub"
    def __init__(self):
        super().__init__()
        self.refresh_calls = 0
    @staticmethod
    def can_handle(run): return 0
    def set_run(self, run): ...
    def get_streams(self): return []
    def set_stream(self, name): ...
    def get_fields(self): return []
    def set_field(self, name): ...
    def refresh(self): self.refresh_calls += 1


def test_on_stream_update_defaults_to_refresh(qapp):
    viz = _StubViz()
    viz.on_stream_update({"type": "array-data"})
    assert viz.refresh_calls == 1
