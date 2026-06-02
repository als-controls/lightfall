"""Fit panel UI for interactive curve fitting.

Provides a panel for selecting fit function, fitting data, and
displaying results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from loguru import logger
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lightfall.visualization.fitting.base import FitResult
from lightfall.visualization.fitting.fitters import get_fitter, list_fitters

if TYPE_CHECKING:
    from lightfall.visualization.widgets.plot_1d import Plot1DVisualization


class FitPanel(QWidget):
    """Panel for interactive curve fitting.

    Provides controls for:
    - Selecting fit function type
    - Setting fit region (x range)
    - Executing fit
    - Displaying fit results

    Signals:
        fit_complete(FitResult): Emitted when a fit completes.
    """

    fit_complete = Signal(object)  # FitResult

    def __init__(
        self,
        plot: Plot1DVisualization | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the fit panel.

        Args:
            plot: Plot1DVisualization to fit (optional, can set later).
            parent: Parent widget.
        """
        super().__init__(parent)
        self._plot = plot
        self._last_result: FitResult | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Fit function selection
        func_group = QGroupBox("Fit Function")
        func_layout = QFormLayout(func_group)

        self._function_combo = QComboBox()
        for fitter_info in list_fitters():
            self._function_combo.addItem(
                fitter_info["display_name"],
                fitter_info["name"],
            )
        func_layout.addRow("Function:", self._function_combo)

        # Formula display
        self._formula_label = QLabel("")
        self._formula_label.setStyleSheet("font-family: monospace;")
        func_layout.addRow("Formula:", self._formula_label)

        self._function_combo.currentIndexChanged.connect(self._on_function_changed)
        self._on_function_changed()  # Initialize formula

        layout.addWidget(func_group)

        # Fit region
        region_group = QGroupBox("Fit Region")
        region_layout = QFormLayout(region_group)

        self._x_min_edit = QLineEdit()
        self._x_min_edit.setPlaceholderText("auto")
        region_layout.addRow("X Min:", self._x_min_edit)

        self._x_max_edit = QLineEdit()
        self._x_max_edit.setPlaceholderText("auto")
        region_layout.addRow("X Max:", self._x_max_edit)

        layout.addWidget(region_group)

        # Fit button
        btn_layout = QHBoxLayout()
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.clicked.connect(self._on_fit_clicked)
        btn_layout.addWidget(self._fit_btn)

        self._clear_btn = QPushButton("Clear Fit")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        btn_layout.addWidget(self._clear_btn)

        layout.addLayout(btn_layout)

        # Results display
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)

        self._results_text = QTextEdit()
        self._results_text.setReadOnly(True)
        self._results_text.setMaximumHeight(150)
        results_layout.addWidget(self._results_text)

        layout.addWidget(results_group)

        # Spacer
        layout.addStretch()

    def _on_function_changed(self) -> None:
        """Handle function selection change."""
        name = self._function_combo.currentData()
        if name:
            try:
                fitter = get_fitter(name)
                self._formula_label.setText(fitter.get_formula())
            except Exception:
                self._formula_label.setText("")

    def _on_fit_clicked(self) -> None:
        """Handle fit button click."""
        if not self._plot:
            logger.warning("No plot connected for fitting")
            return

        # Get data from plot
        x_data, y_data = self._plot.get_data_arrays()
        if len(x_data) == 0:
            self._show_error("No data to fit")
            return

        # Get fit region
        x_min, x_max = self._get_fit_region(x_data)

        # Filter data to region
        mask = (x_data >= x_min) & (x_data <= x_max)
        x_fit = x_data[mask]
        y_fit = y_data[mask]

        if len(x_fit) < 3:
            self._show_error("Not enough points in fit region")
            return

        # Get fitter
        fitter_name = self._function_combo.currentData()
        try:
            fitter = get_fitter(fitter_name)
        except ValueError as e:
            self._show_error(str(e))
            return

        # Perform fit
        result = fitter.fit(x_fit, y_fit)

        self._last_result = result
        self._display_result(result)

        # Update plot with fit curve
        if result.success and result.x_fit is not None and result.y_fit is not None:
            self._plot.set_fit_data(result.x_fit, result.y_fit)

        self.fit_complete.emit(result)

    def _on_clear_clicked(self) -> None:
        """Handle clear fit button click."""
        if self._plot:
            self._plot.clear_fit()
        self._results_text.clear()
        self._last_result = None

    def _get_fit_region(
        self, x_data: np.ndarray
    ) -> tuple[float, float]:
        """Get fit region from inputs or data range.

        Args:
            x_data: X data array.

        Returns:
            Tuple of (x_min, x_max).
        """
        try:
            x_min = float(self._x_min_edit.text())
        except ValueError:
            x_min = float(x_data.min())

        try:
            x_max = float(self._x_max_edit.text())
        except ValueError:
            x_max = float(x_data.max())

        return x_min, x_max

    def _display_result(self, result: FitResult) -> None:
        """Display fit result in the text area.

        Args:
            result: Fit result to display.
        """
        lines = []

        if result.success:
            lines.append("Fit successful")
            lines.append(f"R² = {result.r_squared:.6f}")
            lines.append("")
            lines.append("Parameters:")

            for name in result.parameters:
                val, err = result.get_parameter(name)
                lines.append(f"  {name}: {val:.6g} ± {err:.6g}")

        else:
            lines.append("Fit failed")
            if "error" in result.info:
                lines.append(f"Error: {result.info['error']}")

        self._results_text.setPlainText("\n".join(lines))

    def _show_error(self, message: str) -> None:
        """Display error message.

        Args:
            message: Error message.
        """
        self._results_text.setPlainText(f"Error: {message}")

    def set_plot(self, plot: Plot1DVisualization) -> None:
        """Set the plot widget to fit.

        Args:
            plot: Plot1DVisualization instance.
        """
        self._plot = plot

    def get_last_result(self) -> FitResult | None:
        """Get the last fit result.

        Returns:
            Last FitResult or None.
        """
        return self._last_result
