from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui_components import create_popup_window

if TYPE_CHECKING:
    pass


class SettingsPanel:
    """Manages the global settings popup UI, fully decoupled from any specific overlay."""

    def __init__(self) -> None:
        # State accessors
        self.get_profiles: Callable[[], tuple[str | None, list[str]]] | None = None
        self.get_keep_caught: Callable[[], bool] | None = None
        self.get_auto_switch: Callable[[], bool] | None = None
        self.get_click_to_catch: Callable[[], bool] | None = None
        self.get_current_region: Callable[[], str | None] | None = None
        self.get_dex_scale: Callable[[], float | None] | None = None
        self.get_battle_scale: Callable[[], float | None] | None = None

        # Mutators
        self.on_choose_profile: Callable[[str], None] | None = None
        self.on_remove_profile: Callable[[str], None] | None = None
        self.on_create_profile: Callable[[str], None] | None = None
        self.on_toggle_keep_caught: Callable[[], None] | None = None
        self.on_toggle_auto_switch: Callable[[], None] | None = None
        self.on_toggle_click_to_catch: Callable[[], None] | None = None
        self.on_override_region: Callable[[str | None], None] | None = None
        self.on_set_dex_scale: Callable[[float | None], None] | None = None
        self.on_set_battle_scale: Callable[[float | None], None] | None = None
        self.on_dump_debug: Callable[[], None] | None = None

        self._popup: QWidget | None = None
        self._mono = QFont("Consolas")
        self._mono.setStyleHint(QFont.StyleHint.Monospace)

    def _font(self, size_px: int, bold: bool = False) -> QFont:
        f = QFont(self._mono)
        f.setPixelSize(size_px)
        f.setBold(bold)
        return f

    def close(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None

    def show(
        self,
        mode: str = "dex",
        anchor_pos: QPoint | None = None,
        parent_widget: QWidget | None = None,
    ) -> None:
        if self._popup is not None and self._popup.isVisible():
            if getattr(self, "_active_mode", None) == mode:
                self.close()
                return
            self.close()

        self._active_mode = mode
        if self._popup is not None:
            self.close()

        w, box = create_popup_window("profiles", parent_widget)
        self._popup = w

        self._build_ui(box)

        if anchor_pos is not None:
            self._popup.move(anchor_pos)
        self._popup.show()
        self._popup.raise_()

    def _refresh(self) -> None:
        """Rebuilds the UI in-place to reflect updated state while keeping the popup open."""
        if self._popup is not None:
            pos = self._popup.pos()
            parent = self._popup.parentWidget()
            mode = getattr(self, "_active_mode", "dex")
            self.show(mode, pos, parent)

    def _build_ui(self, box: QVBoxLayout) -> None:
        active, accounts = self.get_profiles() if self.get_profiles else (None, [])

        head = QLabel("Profiles")
        head.setFont(self._font(12, bold=True))
        head.setObjectName("PrimaryText")
        box.addWidget(head)

        for name in accounts:
            row = QHBoxLayout()
            row.setSpacing(8)
            sw = QPushButton(("● " if name == active else "    ") + name)
            sw.setFont(self._font(12))
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.setObjectName("LeftAlignBtn")
            sw.clicked.connect(lambda _=False, n=name: self._handle_choose_profile(n))

            minus = QPushButton("−")
            minus.setFont(self._font(14, bold=True))
            minus.setCursor(Qt.CursorShape.PointingHandCursor)
            minus.setToolTip(f"Delete profile '{name}'")
            minus.setFixedWidth(20)
            minus.clicked.connect(lambda _=False, n=name: self._handle_remove_profile(n))

            row.addWidget(sw, 1)
            row.addWidget(minus)
            cont = QWidget()
            cont.setLayout(row)
            box.addWidget(cont)

        new = QPushButton("+  New profile…")
        new.setFont(self._font(12))
        new.setCursor(Qt.CursorShape.PointingHandCursor)
        new.setObjectName("LeftAlignBtnSecondary")
        new.clicked.connect(self._handle_create_profile)
        box.addWidget(new)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Divider")
        box.addWidget(sep)

        dex_head = QLabel("Dex")
        dex_head.setFont(self._font(12, bold=True))
        dex_head.setObjectName("PrimaryText")
        box.addWidget(dex_head)

        keep = self.get_keep_caught() if self.get_keep_caught is not None else True
        toggle = QPushButton(("✓  " if keep else "    ") + "Show caught")
        toggle.setFont(self._font(12))
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        toggle.setToolTip("Keep caught species in the list, checked, at the bottom")
        toggle.setObjectName("LeftAlignBtnChecked" if keep else "LeftAlignBtnUnchecked")
        toggle.clicked.connect(self._handle_toggle_keep_caught)
        box.addWidget(toggle)

        auto_switch = self.get_auto_switch() if self.get_auto_switch is not None else True
        auto_toggle = QPushButton(("✓  " if auto_switch else "    ") + "Auto-switch mode")
        auto_toggle.setFont(self._font(12))
        auto_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        auto_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        auto_toggle.setToolTip(
            "Automatically switch to Battle Mode when a battle starts, "
            "and Dex Mode in the overworld."
        )
        auto_toggle.setObjectName("LeftAlignBtnChecked" if auto_switch else "LeftAlignBtnUnchecked")
        auto_toggle.clicked.connect(self._handle_toggle_auto_switch)
        box.addWidget(auto_toggle)

        click_to_catch = self.get_click_to_catch() if self.get_click_to_catch is not None else True
        click_toggle = QPushButton(("✓  " if click_to_catch else "    ") + "Click to mark caught")
        click_toggle.setFont(self._font(12))
        click_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        click_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        click_toggle.setToolTip("Click a species in the list to manually toggle its caught status.")
        click_toggle.setObjectName(
            "LeftAlignBtnChecked" if click_to_catch else "LeftAlignBtnUnchecked"
        )
        click_toggle.clicked.connect(self._handle_toggle_click_to_catch)
        box.addWidget(click_toggle)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setObjectName("Divider")
        box.addWidget(sep2)

        reg_head = QLabel("Region Override")
        reg_head.setFont(self._font(12, bold=True))
        reg_head.setObjectName("PrimaryText")
        box.addWidget(reg_head)

        reg_grid = QGridLayout()
        reg_grid.setContentsMargins(0, 0, 0, 0)
        reg_grid.setSpacing(4)
        curr = self.get_current_region() if self.get_current_region is not None else None
        for i, reg in enumerate(["Auto", "Kanto", "Johto", "Hoenn", "Sinnoh", "Unova"]):
            btn = QPushButton(reg)
            btn.setFont(self._font(12))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            is_active = (curr is not None and curr.lower() == reg.lower()) or (
                curr is None and reg == "Auto"
            )
            btn.setObjectName("RegionBtn" if is_active else "RegionBtnInactive")
            btn.clicked.connect(lambda _=False, r=reg: self._handle_region_changed(r))
            reg_grid.addWidget(btn, i // 3, i % 3)

        cont2 = QWidget()
        cont2.setLayout(reg_grid)
        box.addWidget(cont2)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setObjectName("Divider")
        box.addWidget(sep3)

        # --- UI SCALE ---
        mode = getattr(self, "_active_mode", "dex")
        scale_head_layout = QHBoxLayout()
        scale_head = QLabel("UI Scale")
        scale_head.setFont(self._font(12, bold=True))
        scale_head.setObjectName("PrimaryText")

        self._scale_val_label = QLabel("1.00x")
        self._scale_val_label.setFixedWidth(40)
        self._scale_val_label.setObjectName("PrimaryText")

        scale_head_layout.addWidget(scale_head)
        scale_head_layout.addStretch(1)
        scale_head_layout.addWidget(self._scale_val_label)

        scale_head_w = QWidget()
        scale_head_w.setLayout(scale_head_layout)
        scale_head_layout.setContentsMargins(0, 0, 0, 0)
        box.addWidget(scale_head_w)

        scale_row = QHBoxLayout()

        self._scale_auto_cb = QCheckBox("Auto")
        self._scale_auto_cb.setFont(self._font(11))
        self._scale_auto_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._scale_slider = QSlider(Qt.Orientation.Horizontal)
        self._scale_slider.setRange(10, 200)
        self._scale_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scale_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        scale_row.addWidget(self._scale_auto_cb)
        scale_row.addWidget(self._scale_slider)
        box.addLayout(scale_row)

        if mode == "dex":
            curr_scale = self.get_dex_scale() if self.get_dex_scale else None
        else:
            curr_scale = self.get_battle_scale() if self.get_battle_scale else None

        if curr_scale is not None:
            self._scale_auto_cb.setChecked(False)
            self._scale_slider.setEnabled(True)
            self._scale_slider.setValue(int(curr_scale * 100))
            self._scale_val_label.setText(f"{curr_scale:.2f}x")
        else:
            self._scale_auto_cb.setChecked(True)
            self._scale_slider.setEnabled(False)
            self._scale_slider.setValue(100)
            self._scale_val_label.setText("Auto")

        self._scale_slider.valueChanged.connect(self._scale_slider_changed)
        self._scale_auto_cb.stateChanged.connect(self._scale_auto_changed)

        sep4 = QFrame()
        sep4.setFrameShape(QFrame.Shape.HLine)
        sep4.setObjectName("Divider")
        box.addWidget(sep4)

        dump_btn = QPushButton("Dump OCR Debug Images")
        dump_btn.setFont(self._font(12, bold=True))
        dump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dump_btn.setObjectName("LeftAlignBtnSecondary")
        dump_btn.setToolTip("Save all currently processed OCR crops to disk for debugging false positives.")
        dump_btn.clicked.connect(self._handle_dump_debug)
        box.addWidget(dump_btn)

    # --- Internal Event Handlers ---
    def _handle_choose_profile(self, name: str) -> None:
        if self.on_choose_profile:
            self.on_choose_profile(name)
        self._refresh()

    def _handle_remove_profile(self, name: str) -> None:
        from PyQt6.QtWidgets import QMessageBox

        if self._popup is None:
            return
        ok = QMessageBox.question(
            self._popup, "Delete profile", f"Delete profile '{name}' and its caught list?"
        )
        if ok == QMessageBox.StandardButton.Yes and self.on_remove_profile is not None:
            self.on_remove_profile(name)
            self._refresh()

    def _handle_create_profile(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        if self._popup is None:
            return
        name, ok = QInputDialog.getText(self._popup, "New profile", "Account name:")
        if ok and name.strip() and self.on_create_profile is not None:
            self.on_create_profile(name.strip())
            self._refresh()

    def _handle_toggle_keep_caught(self) -> None:
        if self.on_toggle_keep_caught:
            self.on_toggle_keep_caught()
        self._refresh()

    def _handle_toggle_auto_switch(self) -> None:
        if self.on_toggle_auto_switch:
            self.on_toggle_auto_switch()
        self._refresh()

    def _handle_toggle_click_to_catch(self) -> None:
        if self.on_toggle_click_to_catch:
            self.on_toggle_click_to_catch()
        self._refresh()

    def _handle_region_changed(self, reg: str) -> None:
        if self.on_override_region:
            self.on_override_region(reg if reg != "Auto" else None)
        self.close()

    def _scale_auto_changed(self, state: int) -> None:
        if state == Qt.CheckState.Checked.value:
            self._scale_slider.setEnabled(False)
            self._scale_val_label.setText("Auto")
            mode = getattr(self, "_active_mode", "dex")
            if mode == "dex" and self.on_set_dex_scale is not None:
                self.on_set_dex_scale(None)
            elif mode == "battle" and self.on_set_battle_scale is not None:
                self.on_set_battle_scale(None)
        else:
            self._scale_slider.setEnabled(True)
            self._scale_slider_changed(self._scale_slider.value())

    def _scale_slider_changed(self, value: int) -> None:
        if not self._scale_auto_cb.isChecked():
            scale = value / 100.0
            self._scale_val_label.setText(f"{scale:.2f}x")
            mode = getattr(self, "_active_mode", "dex")
            if mode == "dex" and self.on_set_dex_scale is not None:
                self.on_set_dex_scale(scale)
            elif mode == "battle" and self.on_set_battle_scale is not None:
                self.on_set_battle_scale(scale)

    def _handle_dump_debug(self) -> None:
        if self.on_dump_debug:
            self.on_dump_debug()
        self.close()
