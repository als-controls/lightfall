"""Tests for UI annotation metadata extraction and application."""

from __future__ import annotations

from typing import Annotated, Any

import pytest

from lightfall.ui.annotations import (
    Decimals,
    Default,
    DeviceDefault,
    DeviceFilter,
    DeviceFilterAny,
    Range,
    Unit,
)
from lightfall.acquire.plans import PlanInfo
from lightfall.ui.widgets.plan_config import extract_annotated_metadata


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



class TestPlanConfigBuildParamSpec:
    """Tests for PlanConfigWidget._build_param_spec method."""

    @pytest.fixture
    def config_widget(self, qapp):
        """Create a PlanConfigWidget instance."""
        from lightfall.ui.widgets.plan_config import PlanConfigWidget

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
        """DeviceFilter annotation creates device parameter with categories."""
        from lightfall.devices.model import DeviceCategory

        def plan(
            motor: Annotated[Any, DeviceFilter(category="motor")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        motor_param = root.child("motor")
        assert motor_param is not None
        assert motor_param.opts["type"] == "device"
        assert motor_param.opts["categories"] == {DeviceCategory.MOTOR}
        assert motor_param.opts["multi_select"] is False

    def test_device_param_with_multi_category(self, config_widget):
        """DeviceFilter with set category creates multi-category filter."""
        from lightfall.devices.model import DeviceCategory

        def plan(
            devices: Annotated[list, DeviceFilter(category={"motor", "controller"})],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        param = root.child("devices")
        assert param is not None
        assert param.opts["categories"] == {
            DeviceCategory.MOTOR, DeviceCategory.CONTROLLER
        }

    def test_device_param_with_icon(self, config_widget):
        """DeviceIcon annotation sets icon on parameter spec."""
        from lightfall.ui.annotations import DeviceIcon

        def plan(
            motor: Annotated[Any, DeviceFilter(category="motor"), DeviceIcon("mdi6.engine")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        motor_param = root.child("motor")
        assert motor_param.opts.get("icon") == "mdi6.engine"

    def test_device_default_becomes_initial_selection(self, config_widget):
        """DeviceDefault translates to initial value on the parameter."""
        def plan(
            detectors: Annotated[list, DeviceFilter(category="detector"), DeviceDefault("det1")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        param = root.child("detectors")
        assert param is not None
        assert param.value() == ["det1"]

    def test_device_param_with_filter_any(self, config_widget):
        """DeviceFilterAny annotation extracts categories."""
        from lightfall.devices.model import DeviceCategory

        def plan(
            axis: Annotated[Any, DeviceFilterAny(
                DeviceFilter(category="motor"),
                DeviceFilter(category="controller"),
            )],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        axis_param = root.child("axis")
        assert axis_param is not None
        assert axis_param.opts["type"] == "device"
        assert axis_param.opts["categories"] == {DeviceCategory.MOTOR, DeviceCategory.CONTROLLER}

    def test_device_param_with_default(self, config_widget):
        """DeviceDefault annotation sets initial value."""
        def plan(
            detectors: Annotated[list, DeviceFilter(category="detector"),
                                 DeviceDefault("det1", "det2")],
        ):
            pass

        plan_info = PlanInfo.from_function("test", plan, "test")
        config_widget.set_plan(plan_info)

        root = config_widget._root_param
        param = root.child("detectors")
        assert param is not None
        assert param.opts["type"] == "device"
        assert param.opts["multi_select"] is True
        assert param.value() == ["det1", "det2"]


class TestDeviceIcon:
    """Tests for DeviceIcon annotation."""

    def test_device_icon_stores_name(self):
        """DeviceIcon stores the icon name."""
        from lightfall.ui.annotations import DeviceIcon

        icon = DeviceIcon("mdi6.engine")
        assert icon.name == "mdi6.engine"

    def test_device_icon_is_frozen(self):
        """DeviceIcon is immutable."""
        from lightfall.ui.annotations import DeviceIcon

        icon = DeviceIcon("mdi6.engine")
        with pytest.raises(AttributeError):
            icon.name = "other"


class TestDeviceFilterMultiCategory:
    """Tests for DeviceFilter multi-category support."""

    def test_category_as_string(self):
        """DeviceFilter.category accepts a string (backwards compatible)."""
        from lightfall.ui.annotations import DeviceFilter

        flt = DeviceFilter(category="motor")
        assert flt.category == "motor"

    def test_category_as_set(self):
        """DeviceFilter.category accepts a set of strings."""
        from lightfall.ui.annotations import DeviceFilter

        flt = DeviceFilter(category={"motor", "controller"})
        assert flt.category == {"motor", "controller"}

    def test_category_default_none(self):
        """DeviceFilter.category defaults to None."""
        from lightfall.ui.annotations import DeviceFilter

        flt = DeviceFilter()
        assert flt.category is None
