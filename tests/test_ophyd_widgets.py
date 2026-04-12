"""Tests for ophyd-based reusable widgets."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lucid.epics.widgets.ophyd_base import OphydWidget
from lucid.epics.widgets.ophyd_lineedit import OphydLineEdit
from lucid.epics.widgets.ophyd_label import OphydLabel
from lucid.epics.widgets.ophyd_combobox import OphydComboBox
from lucid.epics.widgets.ophyd_spinbox import OphydSpinBox


class ConcreteOphydWidget(OphydWidget):
    """Concrete subclass for testing abstract base."""

    def _update_display(self) -> None:
        pass

    def _get_widget_value(self):
        return self._value

    def _set_widget_value(self, value) -> None:
        self._value = value


@pytest.fixture
def widget(qtbot):
    w = ConcreteOphydWidget()
    qtbot.addWidget(w)
    return w


class TestOphydWidgetBase:
    def test_initial_state(self, widget):
        assert widget.signal is None
        assert widget._value is None
        assert widget._connected is False
        assert widget.readonly is False

    def test_set_signal_stores_reference(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 42.0
        widget.signal = sig
        assert widget.signal is sig

    def test_readonly_prevents_write(self, widget):
        widget.readonly = True
        with pytest.raises(RuntimeError, match="readonly"):
            widget.write_value(42)

    def test_disconnected_prevents_write(self, widget):
        with pytest.raises(RuntimeError, match="not connected"):
            widget.write_value(42)

    def test_set_signal_subscribes(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        widget.signal = sig
        sig.subscribe.assert_called_once()

    def test_clear_signal_unsubscribes(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        sig.subscribe.return_value = 42
        widget.signal = sig
        widget.signal = None
        sig.unsubscribe.assert_called_once_with(42)

    def test_initial_value_read(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 3.14
        widget.signal = sig
        assert widget._value == 3.14

    def test_polling_fallback_on_subscribe_failure(self, widget, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        sig.subscribe.side_effect = RuntimeError("no subscribe")
        widget.signal = sig
        assert widget._poll_timer is not None
        assert widget._poll_timer.isActive()

    def test_write_value_calls_put(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        sig.put.return_value = None
        widget.signal = sig
        widget.write_value(99)
        sig.put.assert_called_once_with(99)

    def test_write_value_uses_widget_value_when_none(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 7
        sig.put.return_value = None
        widget.signal = sig
        widget.write_value()
        sig.put.assert_called_once_with(7)

    def test_write_falls_back_to_set(self, widget):
        sig = MagicMock(spec=["subscribe", "get", "connected", "set", "unsubscribe"])
        sig.connected = True
        sig.get.return_value = 0
        sig.subscribe.return_value = 1
        widget.signal = sig
        widget.write_value(5)
        sig.set.assert_called_once_with(5)

    def test_connection_style_applied(self, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        widget.signal = sig
        # Connected: style should be empty (WidgetStyles.connected() returns "")
        assert widget.styleSheet() == ""

    def test_disconnected_style_applied(self, widget):
        # Not connected by default
        assert "background-color" in widget.styleSheet()

    @patch("lucid.epics.widgets.ophyd_base.inspect.isawaitable", return_value=True)
    def test_async_get_skipped(self, mock_awaitable, widget):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = MagicMock()  # fake coroutine
        widget.signal = sig
        # Value should remain None since awaitable is skipped
        assert widget._value is None

    def test_close_disconnects(self, widget, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        sig.subscribe.return_value = 10
        widget.signal = sig
        widget.close()
        sig.unsubscribe.assert_called_once_with(10)
        assert widget._signal is None

    def test_on_signal_value_scalar_unwrap(self, widget, qtbot):
        """Single-element array values should be unwrapped."""
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        widget.signal = sig

        # Simulate callback with single-element list
        widget._on_signal_value(value=[42.0])
        assert widget._value == 42.0


class TestOphydLineEdit:
    def test_displays_value(self, qtbot):
        w = OphydLineEdit()
        qtbot.addWidget(w)
        w._value = 3.14159
        w._update_display()
        assert w._line_edit.text() == "3.1416"

    def test_write_on_enter(self, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0.0
        w = OphydLineEdit(write_on_enter=True)
        qtbot.addWidget(w)
        w.signal = sig
        w._connected = True
        w._line_edit.setText("42.0")
        w._on_return_pressed()
        sig.put.assert_called_once_with(42.0)

    def test_modified_style(self, qtbot):
        w = OphydLineEdit()
        qtbot.addWidget(w)
        w._value = 1.0
        w._update_display()
        w._line_edit.setText("2.0")
        assert w._modified is True

    def test_readonly_disables_editing(self, qtbot):
        w = OphydLineEdit(readonly=True)
        qtbot.addWidget(w)
        assert w._line_edit.isReadOnly()

    def test_precision(self, qtbot):
        w = OphydLineEdit(precision=2)
        qtbot.addWidget(w)
        w._value = 3.14159
        w._update_display()
        assert w._line_edit.text() == "3.14"

    def test_int_coercion(self, qtbot):
        w = OphydLineEdit()
        qtbot.addWidget(w)
        w._value = 10
        w._line_edit.setText("42")
        assert w._get_widget_value() == 42
        assert isinstance(w._get_widget_value(), int)


class TestOphydLabel:
    def test_displays_value(self, qtbot):
        w = OphydLabel()
        qtbot.addWidget(w)
        w._value = 25.678
        w._update_display()
        assert w._value_label.text() == "25.6780"

    def test_displays_none_as_dashes(self, qtbot):
        w = OphydLabel()
        qtbot.addWidget(w)
        w._value = None
        w._update_display()
        assert w._value_label.text() == "---"

    def test_custom_precision(self, qtbot):
        w = OphydLabel(precision=2)
        qtbot.addWidget(w)
        w._value = 3.14159
        w._update_display()
        assert w._value_label.text() == "3.14"

    def test_int_display(self, qtbot):
        w = OphydLabel()
        qtbot.addWidget(w)
        w._value = 42
        w._update_display()
        assert w._value_label.text() == "42"


class TestOphydComboBox:
    def test_set_items(self, qtbot):
        w = OphydComboBox()
        qtbot.addWidget(w)
        w.set_items(["Single", "Multiple", "Continuous"])
        assert w._combo.count() == 3
        assert w._combo.itemText(1) == "Multiple"

    def test_displays_value_as_index(self, qtbot):
        w = OphydComboBox()
        qtbot.addWidget(w)
        w.set_items(["Off", "On"])
        w._value = 1
        w._update_display()
        assert w._combo.currentIndex() == 1

    def test_write_on_change(self, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0
        w = OphydComboBox(write_on_change=True)
        qtbot.addWidget(w)
        w.set_items(["Off", "On"])
        w.signal = sig
        w._connected = True
        w._combo.setCurrentIndex(1)
        sig.put.assert_called_with(1)

    def test_readonly_disables(self, qtbot):
        w = OphydComboBox(readonly=True)
        qtbot.addWidget(w)
        assert not w._combo.isEnabled()


class TestOphydSpinBox:
    def test_displays_value(self, qtbot):
        w = OphydSpinBox(minimum=-100.0, maximum=50.0)
        qtbot.addWidget(w)
        w._value = -20.5
        w._update_display()
        assert w._spinbox.value() == -20.5

    def test_range(self, qtbot):
        w = OphydSpinBox(minimum=0.0, maximum=100.0)
        qtbot.addWidget(w)
        assert w._spinbox.minimum() == 0.0
        assert w._spinbox.maximum() == 100.0

    def test_write_on_change(self, qtbot):
        sig = MagicMock()
        sig.connected = True
        sig.get.return_value = 0.0
        w = OphydSpinBox(write_on_change=True)
        qtbot.addWidget(w)
        w.signal = sig
        w._connected = True
        w._spinbox.setValue(42.0)
        sig.put.assert_called_with(42.0)

    def test_readonly(self, qtbot):
        w = OphydSpinBox(readonly=True)
        qtbot.addWidget(w)
        assert w._spinbox.isReadOnly()
