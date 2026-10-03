"""Issue #2: both Qt entry points, desktop fallback and folder hand-off."""

import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication

from litmetica3d import __version__, desktop, gui_app, gui_qt


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture(params=[gui_app, gui_qt], ids=["presets", "legacy"])
def window(request, tmp_path, monkeypatch, qt_app):
    def create_settings(*_args):
        return QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)

    monkeypatch.setattr(request.param, "QSettings", create_settings)
    ui = request.param.MainWindow()
    yield ui
    ui.close()
    qt_app.processEvents()


def set_geometry(window, value):
    if hasattr(window, "geometry_combo"):
        window._set_value(window.geometry_combo, value)
    else:
        window._set_geometry(value)


def enable_visual(window):
    window._set_value(window.format_combo, "obj")
    set_geometry(window, "visual")
    if hasattr(window, "emission_check"):
        window.emission_check.setChecked(True)


def test_versions_layout_and_automatic_optimization(window):
    assert f"v{__version__}" in window.windowTitle()
    assert not hasattr(window, "optimize_combo")
    assert window._snapshot()["optimize"] == "safe"
    assert window.pages.count() == (3 if isinstance(window, gui_app.MainWindow) else 4)


def test_print_only_options_are_disabled_and_normalized_in_visual(window):
    window._set_value(window.components_combo, "remove-small")
    window._set_value(window.cavities_combo, "fill")
    window._set_value(window.boolean_combo, "fail")
    window.component_spin.setValue(0.75)
    assert window.component_spin.isEnabled()
    assert not window.color_check.isEnabled()

    enable_visual(window)
    for widget in (
        window.components_combo, window.cavities_combo,
        window.boolean_combo, window.component_spin,
    ):
        assert not widget.isEnabled()
    snapshot = window._snapshot()
    assert snapshot["geometry"] == "visual"
    assert snapshot["components"] == "keep"
    assert snapshot["cavities"] == "preserve"
    assert snapshot["boolean_fallback"] == "voxel32"
    assert snapshot["min_component_volume"] == 1 / 4096
    assert snapshot["textures"] is True
    assert window.textures_check.isChecked()
    assert not window.textures_check.isEnabled()
    assert window.color_check.isEnabled()
    if hasattr(window, "preset_summary"):
        window._update_custom_summary()
        summary = window.preset_summary.toPlainText()
        assert "独立壳体：keep" in summary
        assert "空腔：preserve" in summary

    set_geometry(window, "print")
    for widget in (
        window.components_combo, window.cavities_combo,
        window.boolean_combo, window.component_spin,
    ):
        assert widget.isEnabled()
    restored = window._snapshot()
    assert restored["components"] == "remove-small"
    assert restored["cavities"] == "fill"
    assert restored["boolean_fallback"] == "fail"
    assert restored["min_component_volume"] == 0.75
    window._set_value(window.components_combo, "keep")
    assert not window.component_spin.isEnabled()
    assert window._snapshot()["min_component_volume"] == 1 / 4096


@pytest.mark.parametrize("mode", ["none", "material", "exact", "clustered"])
def test_emission_controls_and_snapshot_agree(window, mode):
    enable_visual(window)
    window._set_value(window.emission_combo, mode)
    window.emission_strength_spin.setValue(3.5)
    window.emission_config_edit.setText("  中文 空格规则.json  ")
    active = mode != "none"
    snapshot = window._snapshot()
    assert snapshot["textures"] is True
    assert snapshot["emission"] is active
    assert snapshot["blender_lights"] == mode
    assert snapshot["emission_strength"] == (3.5 if active else 1.0)
    assert snapshot["emission_config"] == ("中文 空格规则.json" if active else "")
    assert window.emission_combo.isEnabled()
    assert window.emission_strength_spin.isEnabled() is active
    assert window.emission_config_edit.isEnabled() is active
    assert window.emission_config_button.isEnabled() is active


def test_stl_switch_resolves_visual_state(window):
    enable_visual(window)
    window._set_value(window.emission_combo, "exact")
    window.color_check.setChecked(True)
    window.emission_config_edit.setText("stale.json")
    window._set_value(window.format_combo, "stl")
    if hasattr(window, "geometry_combo"):
        assert window._value(window.geometry_combo) == "print"
        assert not window.geometry_combo.isEnabled()
    else:
        assert window.geometry == "print"
        assert window.print_mode.isChecked()
        assert not window.visual_mode.isEnabled()
    snapshot = window._snapshot()
    assert snapshot["geometry"] == "print"
    assert snapshot["textures"] is False
    assert snapshot["color"] is False
    assert snapshot["emission"] is False
    assert snapshot["blender_lights"] == "none"
    assert snapshot["emission_config"] == ""
    assert snapshot["emission_strength"] == 1.0
    assert not window.textures_check.isChecked()
    assert not window.emission_combo.isEnabled()
    assert not window.emission_config_button.isEnabled()
    assert not window.emission_strength_spin.isEnabled()
    assert window.components_combo.isEnabled()


def test_snapshot_normalizes_even_with_blocked_change_signals(window):
    enable_visual(window)
    window._set_value(window.emission_combo, "exact")
    window.color_check.setChecked(True)
    blocked = window.format_combo.blockSignals(True)
    try:
        window._set_value(window.format_combo, "stl")
        snapshot = window._snapshot()
        assert snapshot["geometry"] == "print"
        assert snapshot["textures"] is False
        assert snapshot["color"] is False
        assert snapshot["emission"] is False
        assert snapshot["blender_lights"] == "none"
    finally:
        window.format_combo.blockSignals(blocked)


@pytest.mark.parametrize("already_normalized", [False, True])
def test_open_folder_uses_local_file_url_with_chinese_and_spaces(
    window, tmp_path, monkeypatch, already_normalized,
):
    selected = tmp_path / "中文 空格 & # 输出"
    output = selected / "L3D_output"
    output.mkdir(parents=True)
    opener = Mock(return_value=True)
    warning = Mock()
    information = Mock()
    monkeypatch.delattr(os, "startfile", raising=False)
    monkeypatch.setattr(gui_qt.QDesktopServices, "openUrl", opener)
    monkeypatch.setattr(gui_qt.QMessageBox, "warning", warning)
    monkeypatch.setattr(gui_qt.QMessageBox, "information", information)
    window.output_edit.setText(f"  {output if already_normalized else selected}  ")
    window._open_output()
    opener.assert_called_once()
    url = opener.call_args.args[0]
    assert isinstance(url, QUrl)
    assert url.isLocalFile()
    assert Path(url.toLocalFile()) == output.resolve()
    assert "%20" in url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
    warning.assert_not_called()
    information.assert_not_called()


@pytest.mark.parametrize("failure", [False, OSError("platform hand-off failed")])
def test_open_folder_reports_platform_failure(window, tmp_path, monkeypatch, failure):
    output = tmp_path / "L3D_output"
    output.mkdir()
    opener = Mock(return_value=False, side_effect=failure if failure else None)
    warning = Mock()
    monkeypatch.setattr(gui_qt.QDesktopServices, "openUrl", opener)
    monkeypatch.setattr(gui_qt.QMessageBox, "warning", warning)
    window.output_edit.setText(str(output))
    window._open_output()
    opener.assert_called_once()
    warning.assert_called_once()
    assert "无法打开输出文件夹" in warning.call_args.args[2]


@pytest.mark.parametrize("selection", ["blank", "missing", "file"])
def test_open_folder_never_opens_missing_or_non_directory_targets(
    window, tmp_path, monkeypatch, selection,
):
    target = tmp_path / "L3D_output"
    if selection == "file":
        target.write_text("not a directory", encoding="utf-8")
    opener = Mock()
    information = Mock()
    monkeypatch.setattr(gui_qt.QDesktopServices, "openUrl", opener)
    monkeypatch.setattr(gui_qt.QMessageBox, "information", information)
    window.output_edit.setText("   " if selection == "blank" else str(target))
    window._open_output()
    opener.assert_not_called()
    information.assert_called_once()
    assert not target.is_dir()


@pytest.mark.parametrize("platform,frozen", [("linux", False), ("darwin", False), ("win32", True)])
def test_desktop_uses_qt_for_non_windows_and_frozen_builds(monkeypatch, platform, frozen):
    qt_launcher = Mock(return_value=17)
    native_launcher = Mock()
    monkeypatch.setattr(desktop.sys, "platform", platform)
    monkeypatch.setattr(desktop.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(gui_app, "launch_gui", qt_launcher)
    monkeypatch.setattr(desktop.subprocess, "call", native_launcher)
    assert desktop.launch_gui() == 17
    qt_launcher.assert_called_once_with()
    native_launcher.assert_not_called()


@pytest.mark.parametrize("native_state", ["absent", "ready", "os-error"])
def test_windows_desktop_prefers_winui_but_has_working_qt_fallback(
    tmp_path, monkeypatch, native_state,
):
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(desktop.sys, "frozen", False, raising=False)
    monkeypatch.setattr(desktop, "__file__", str(tmp_path / "litmetica3d" / "desktop.py"))
    exe = tmp_path / (
        "frontend/Litmetica3D.WinUI/bin/x64/Release/"
        "net10.0-windows10.0.26100.0/win-x64/Litmetica3D.WinUI.exe"
    )
    if native_state != "absent":
        exe.parent.mkdir(parents=True)
        exe.touch()
    qt_launcher = Mock(return_value=17)
    native_launcher = Mock(
        return_value=7,
        side_effect=OSError("cannot launch") if native_state == "os-error" else None,
    )
    monkeypatch.setattr(gui_app, "launch_gui", qt_launcher)
    monkeypatch.setattr(desktop.subprocess, "call", native_launcher)
    assert desktop.launch_gui() == (7 if native_state == "ready" else 17)
    if native_state == "absent":
        native_launcher.assert_not_called()
    else:
        native_launcher.assert_called_once_with([str(exe)], cwd=tmp_path)
    if native_state == "ready":
        qt_launcher.assert_not_called()
    else:
        qt_launcher.assert_called_once_with()
