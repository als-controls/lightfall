"""Tests for UI annotation metadata extraction and application."""

from __future__ import annotations

from typing import Annotated, Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from lucid.devices.model import DeviceCategory, DeviceInfo
from lucid.ui.annotations import (
    Decimals,
    Default,
    DeviceDefault,
    DeviceFilter,
    DeviceFilterAny,
    Range,
    Unit,
)
from lucid.ui.widgets.plan_config import extract_annotated_metadata


class TestExtractAnnotatedMetadata:
    """Tests for extract_annotated_metadata function."""

    def test_plain_type_returns_empty_metadata(self):
        """Plain types return the type itself with no metadata."""
        base_type, metadata = extract_annotated_metadata(int)
        assert base_type is int
        assert metadata == []

    def test_annotated_with_single_metadata(self):
        """Annotated with one metadata item."""
        annotation = Annotated[float, Unit("eV")]
        base_type, metadata = extract_annotated_metadata(annotation)
        assert base_type is float
        assert len(metadata) == 1
        assert isinstance(metadata[0], Unit)
        assert metadata[0].suffix == "eV"

    def test_annotated_with_multiple_metadata(self):
        """Annotated with multiple metadata items."""
        annotation = Annotated[float, Unit("eV"), Range(0, 1000), Decimals(4)]
        base_type, metadata = extract_annotated_metadata(annotation)
        assert base_type is float
        assert len(metadata) == 3
        assert metadata[0].suffix == "eV"
        assert metadata[1].min == 0
        assert metadata[1].max == 1000
        assert metadata[2].places == 4

    def test_none_annotation(self):
        """None annotation returns None with empty metadata."""
        base_type, metadata = extract_annotated_metadata(None)
        assert base_type is None
        assert metadata == []


class TestAnnotationDataclasses:
    """Tests for annotation dataclass behavior."""

    def test_unit_is_frozen(self):
        """Unit dataclass is immutable."""
        unit = Unit("mm")
        with pytest.raises(AttributeError):
            unit.suffix = "cm"

    def test_decimals_is_frozen(self):
        """Decimals dataclass is immutable."""
        dec = Decimals(4)
        with pytest.raises(AttributeError):
            dec.places = 5

    def test_range_defaults(self):
        """Range has None defaults for min/max."""
        r = Range()
        assert r.min is None
        assert r.max is None

        r2 = Range(min=0)
        assert r2.min == 0
        assert r2.max is None

    def test_device_filter_defaults(self):
        """DeviceFilter has None defaults for all fields."""
        f = DeviceFilter()
        assert f.device_class is None
        assert f.category is None
        assert f.group is None
        assert f.source is None
        assert f.name_pattern is None

    def test_device_filter_any_variadic_init(self):
        """DeviceFilterAny accepts variadic filter args."""
        f1 = DeviceFilter(category="motor")
        f2 = DeviceFilter(category="positioner")
        any_filter = DeviceFilterAny(f1, f2)
        assert len(any_filter.filters) == 2
        assert any_filter.filters[0].category == "motor"
        assert any_filter.filters[1].category == "positioner"

    def test_device_default_variadic_names(self):
        """DeviceDefault accepts variadic names."""
        default = DeviceDefault("motor1", "motor2", pattern="sample_.*")
        assert default.names == ("motor1", "motor2")
        assert default.pattern == "sample_.*"


class TestDeviceFiltering:
    """Tests for device filtering logic in DeviceSelectorDialog."""

    @pytest.fixture
    def mock_devices(self) -> list[DeviceInfo]:
        """Create mock devices for testing."""
        return [
            DeviceInfo(
                id=uuid4(),
                name="motor1",
                category=DeviceCategory.MOTOR,
                device_class="ophyd.EpicsMotor",
                tags=["beamline1", "magnets"],
            ),
            DeviceInfo(
                id=uuid4(),
                name="motor2",
                category=DeviceCategory.MOTOR,
                device_class="ophyd.EpicsMotor",
                tags=["beamline2"],
            ),
            DeviceInfo(
                id=uuid4(),
                name="positioner1",
                category=DeviceCategory.MOTOR,
                device_class="ophyd.PseudoPositioner",
                tags=["beamline1"],
            ),
            DeviceInfo(
                id=uuid4(),
                name="detector1",
                category=DeviceCategory.DETECTOR,
                device_class="ophyd.AreaDetector",
                tags=["areadetectors", "beamline1"],
            ),
            DeviceInfo(
                id=uuid4(),
                name="sample_x",
                category=DeviceCategory.MOTOR,
                device_class="ophyd.EpicsMotor",
                tags=["sample_stage"],
            ),
        ]

    @pytest.fixture
    def mock_catalog(self, mock_devices: list[DeviceInfo]) -> MagicMock:
        """Create a mock catalog with the mock devices."""
        catalog = MagicMock()
        catalog.get_all_devices.return_value = mock_devices
        return catalog

    def test_filter_by_category(self, mock_devices: list[DeviceInfo]):
        """DeviceFilter filters by category."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        flt = DeviceFilter(category="motor")

        # Simulate the filtering logic
        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        # Import and use the matching method
        matched = [d for d in mock_devices if dialog._matches_single_filter(d, flt)]
        assert len(matched) == 3  # motor1, motor2, sample_x
        assert all(d.category == DeviceCategory.MOTOR for d in matched)

    def test_filter_by_device_class(self, mock_devices: list[DeviceInfo]):
        """DeviceFilter filters by device class."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        flt = DeviceFilter(device_class="ophyd.AreaDetector")

        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        matched = [d for d in mock_devices if dialog._matches_single_filter(d, flt)]
        assert len(matched) == 1
        assert matched[0].name == "detector1"

    def test_filter_by_device_class_short_name(self, mock_devices: list[DeviceInfo]):
        """DeviceFilter matches class name without module path."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        flt = DeviceFilter(device_class="EpicsMotor")

        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        matched = [d for d in mock_devices if dialog._matches_single_filter(d, flt)]
        assert len(matched) == 3  # motor1, motor2, sample_x

    def test_filter_by_group(self, mock_devices: list[DeviceInfo]):
        """DeviceFilter filters by group in tags."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        flt = DeviceFilter(group="areadetectors")

        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        matched = [d for d in mock_devices if dialog._matches_single_filter(d, flt)]
        assert len(matched) == 1
        assert matched[0].name == "detector1"

    def test_filter_by_name_pattern(self, mock_devices: list[DeviceInfo]):
        """DeviceFilter filters by name regex pattern."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        flt = DeviceFilter(name_pattern="sample_.*")

        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        matched = [d for d in mock_devices if dialog._matches_single_filter(d, flt)]
        assert len(matched) == 1
        assert matched[0].name == "sample_x"

    def test_filter_any_or_logic(self, mock_devices: list[DeviceInfo]):
        """DeviceFilterAny uses OR logic between filters."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        flt = DeviceFilterAny(
            DeviceFilter(category="motor"),
            DeviceFilter(category="positioner"),
        )

        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        matched = [d for d in mock_devices if dialog._matches_filter(d)]
        assert len(matched) == 4  # 3 motors + 1 positioner
        categories = {d.category for d in matched}
        assert categories == {DeviceCategory.MOTOR}

    def test_filter_combined_and_logic(self, mock_devices: list[DeviceInfo]):
        """DeviceFilter uses AND logic within a single filter."""
        from lucid.ui.widgets.device_selector import DeviceSelectorDialog

        # Must be both a motor AND have "magnets" tag
        flt = DeviceFilter(category="motor", group="magnets")

        dialog = DeviceSelectorDialog.__new__(DeviceSelectorDialog)
        dialog._device_filter = flt

        matched = [d for d in mock_devices if dialog._matches_single_filter(d, flt)]
        assert len(matched) == 1
        assert matched[0].name == "motor1"


class TestPlanConfigBuildParamSpec:
    """Tests for PlanConfigWidget._build_param_spec method."""

    @pytest.fixture
    def config_widget(self, qapp):
        """Create a PlanConfigWidget instance."""
        from lucid.ui.widgets.plan_config import PlanConfigWidget

        widget = PlanConfigWidget()
        return widget

    def test_basic_float_spec(self, config_widget):
        """Basic float parameter generates correct spec."""

        spec = config_widget._build_param_spec(
            name="energy",
            annotation=float,
            default=100.0,
            doc="Energy in eV",
        )
        assert spec["name"] == "energy"
        assert spec["type"] == "float"
        assert spec["value"] == 100.0
        assert spec["tip"] == "Energy in eV"

    def test_annotated_float_with_unit(self, config_widget):
        """Annotated float with Unit gets suffix."""

        annotation = Annotated[float, Unit("eV")]
        spec = config_widget._build_param_spec(
            name="energy",
            annotation=annotation,
            default=100.0,
            doc=None,
        )
        assert spec["suffix"] == "eV"

    def test_annotated_float_with_decimals(self, config_widget):
        """Annotated float with Decimals gets decimal places."""
        annotation = Annotated[float, Decimals(4)]
        spec = config_widget._build_param_spec(
            name="step",
            annotation=annotation,
            default=0.001,
            doc=None,
        )
        assert spec["decimals"] == 4

    def test_annotated_float_with_range(self, config_widget):
        """Annotated float with Range gets limits."""
        annotation = Annotated[float, Range(0, 1000)]
        spec = config_widget._build_param_spec(
            name="value",
            annotation=annotation,
            default=500.0,
            doc=None,
        )
        assert "limits" in spec
        assert spec["limits"][0] == 0
        assert spec["limits"][1] == 1000

    def test_annotated_int_with_range(self, config_widget):
        """Annotated int with Range gets limits."""
        annotation = Annotated[int, Range(1, 100)]
        spec = config_widget._build_param_spec(
            name="count",
            annotation=annotation,
            default=10,
            doc=None,
        )
        assert "limits" in spec
        assert spec["limits"] == (1, 100)

    def test_annotated_with_default_override(self, config_widget):
        """Default annotation overrides function default."""

        annotation = Annotated[float, Default(42.0)]
        spec = config_widget._build_param_spec(
            name="value",
            annotation=annotation,
            default=100.0,  # Function default
            doc=None,
        )
        # Default annotation should take precedence
        assert spec["value"] == 42.0

    def test_annotated_with_multiple_metadata(self, config_widget):
        """Multiple annotation metadata are all applied."""
        annotation = Annotated[float, Unit("mm"), Decimals(3), Range(0, 100)]
        spec = config_widget._build_param_spec(
            name="position",
            annotation=annotation,
            default=50.0,
            doc="Position in mm",
        )
        assert spec["suffix"] == "mm"
        assert spec["decimals"] == 3
        assert spec["limits"] == (0, 100)
        assert spec["tip"] == "Position in mm"

    def test_device_param_with_filter(self, config_widget):
        """Device parameter with DeviceFilter annotation."""
        # Type hint for a motor device with filter
        annotation = Annotated[Any, DeviceFilter(category="motor")]

        # We need to mock catalog
        config_widget._catalog = MagicMock()

        spec = config_widget._build_param_spec(
            name="motor",  # Name triggers device category
            annotation=annotation,
            default=None,
            doc="Motor to move",
        )
        assert spec["type"] == "device"
        assert spec["multi_select"] is False  # Single device for "motor" name
        assert "device_filter" in spec
        assert spec["device_filter"].category == "motor"

    def test_device_param_with_filter_any(self, config_widget):
        """Device parameter with DeviceFilterAny annotation."""
        # Type hint for motor or positioner
        annotation = Annotated[
            Any,
            DeviceFilterAny(
                DeviceFilter(category="motor"),
                DeviceFilter(category="positioner"),
            ),
        ]

        config_widget._catalog = MagicMock()

        spec = config_widget._build_param_spec(
            name="motor",
            annotation=annotation,
            default=None,
            doc="Motor or positioner",
        )
        assert spec["type"] == "device"
        assert "device_filter" in spec
        assert isinstance(spec["device_filter"], DeviceFilterAny)
        assert len(spec["device_filter"].filters) == 2

    def test_device_param_with_default(self, config_widget):
        """Device parameter with DeviceDefault annotation."""
        annotation = Annotated[
            Any,
            DeviceFilter(category="detector"),
            DeviceDefault("det1", "det2"),
        ]

        config_widget._catalog = MagicMock()

        spec = config_widget._build_param_spec(
            name="detectors",  # Name triggers multi-select
            annotation=annotation,
            default=None,
            doc="Detectors to read",
        )
        assert spec["type"] == "device"
        assert spec["multi_select"] is True
        assert "device_filter" in spec
        assert "device_default" in spec
        assert spec["device_default"].names == ("det1", "det2")
