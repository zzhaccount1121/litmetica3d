import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QAbstractSpinBox

from litmetica3d import gui_app
from litmetica3d.gui_app import MainWindow, VERSION


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    qt_settings = QSettings

    def create_settings(*_args):
        return qt_settings(str(tmp_path / "presets.ini"), qt_settings.Format.IniFormat)

    monkeypatch.setattr(gui_app, "QSettings", create_settings)
    monkeypatch.setitem(globals(), "QSettings", create_settings)


def test_preset_gui_layout_and_values():
    app = QApplication.instance() or QApplication([])
    settings = QSettings("litmetica3d", "litmetica3d-v0.5-presets")
    settings.clear()
    window = MainWindow()
    try:
        assert VERSION == "0.6.3"
        assert window.dark_theme is True
        for spin in (
            window.scale_spin, window.thickness_spin,
            window.component_spin, window.emission_strength_spin,
        ):
            assert spin.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.PlusMinus
        assert window.pages.count() == 3
        assert [button.text() for button in window.nav_buttons] == [
            "预设", "高级", "日志"
        ]

        print_options = window._snapshot()
        assert (
            print_options["format"], print_options["water"],
            print_options["fallback"], print_options["optimize"],
            print_options["geometry"], print_options["components"],
            print_options["cavities"],
        ) == ("stl", "drop", "ignore", "safe", "print", "main", "fill")

        window._apply_preset("visual")
        visual = window._snapshot()
        assert (
            visual["format"], visual["water"], visual["fallback"],
            visual["geometry"], visual["components"], visual["cavities"],
            visual["blender_lights"],
        ) == (
            "obj", "level", "ignore", "visual", "keep", "preserve",
            "material",
        )

        window._apply_preset("render")
        render = window._snapshot()
        assert (
            render["format"], render["water"], render["fallback"],
            render["geometry"], render["components"], render["cavities"],
            render["blender_lights"],
        ) == (
            "obj", "drop", "ignore", "visual", "keep", "preserve", "exact",
        )
        assert len(window.preset_buttons) == 4
        assert window.custom_panel.isHidden()
        window.scale_spin.setValue(2.5)
        assert window.current_preset == "custom"
        assert window.preset_buttons["custom"].isChecked()
        assert not window.custom_panel.isHidden()
        assert "比例：2.5" in window.preset_summary.toPlainText()
        window.dark_theme = False
        window._apply_theme()
        light_style = app.styleSheet()
        assert "#dff4ee" in light_style
        assert "#17332f" not in light_style
        assert "#16302d" not in light_style
        assert window.live_log is not window.report_log
        assert window.preset_summary.isReadOnly()
        assert window.preset_summary.verticalScrollBar() is not None
        window.show()
        app.processEvents()
        assert window.preset_summary.verticalScrollBar().maximum() > 0
    finally:
        window.close()
        settings.clear()
        app.processEvents()

def test_advanced_constraints_and_normalized_snapshot():
    app = QApplication.instance() or QApplication([])
    settings = QSettings("litmetica3d", "litmetica3d-v0.5-presets")
    settings.clear()
    window = MainWindow()
    try:
        # STL must be a print model and must not submit visual-only options.
        printed = window._snapshot()
        assert printed["format"] == "stl"
        assert printed["geometry"] == "print"
        assert printed["textures"] is False
        assert printed["emission"] is False
        assert printed["blender_lights"] == "none"
        assert not window.geometry_combo.isEnabled()
        assert not window.visual_group.isEnabled()
        assert not window.textures_check.isChecked()

        # Every visual model is OBJ and always has textures.
        window._apply_preset("visual")
        visual = window._snapshot()
        assert visual["format"] == "obj"
        assert visual["geometry"] == "visual"
        assert visual["textures"] is True
        assert visual["emission"] is True
        assert window.geometry_combo.isEnabled()
        assert window.visual_group.isEnabled()
        assert window.textures_check.isChecked()
        assert not window.textures_check.isEnabled()
        assert not window.components_combo.isEnabled()
        assert not window.cavities_combo.isEnabled()
        assert not window.boolean_combo.isEnabled()
        assert not window.component_spin.isEnabled()
        assert visual["components"] == "keep"
        assert visual["cavities"] == "preserve"
        window._set_value(window.components_combo, "main")
        window._set_value(window.cavities_combo, "fill")
        window._set_value(window.boolean_combo, "fail")
        visual_custom = window._snapshot()
        assert visual_custom["components"] == "keep"
        assert visual_custom["cavities"] == "preserve"
        assert visual_custom["boolean_fallback"] == "voxel32"
        assert visual_custom["min_component_volume"] == 1 / 4096

        # none keeps visual textures but disables all emission output/editing.
        window._set_value(window.emission_combo, "none")
        no_emission = window._snapshot()
        assert no_emission["textures"] is True
        assert no_emission["emission"] is False
        assert no_emission["blender_lights"] == "none"
        assert not window.emission_strength_spin.isEnabled()
        assert not window.emission_config_edit.isEnabled()
        assert not window.emission_config_button.isEnabled()

        # material uses emissive pixels but does not request Blender lights.
        window._set_value(window.emission_combo, "material")
        material = window._snapshot()
        assert material["textures"] is True
        assert material["emission"] is True
        assert material["blender_lights"] == "material"
        assert window.emission_strength_spin.isEnabled()

        # Switching to STL resolves the incompatible visual state immediately.
        window._set_value(window.format_combo, "stl")
        stl = window._snapshot()
        assert window.current_preset == "custom"
        assert window._value(window.geometry_combo) == "print"
        assert not window.geometry_combo.isEnabled()
        assert not window.visual_group.isEnabled()
        assert stl["geometry"] == "print"
        assert stl["textures"] is False
        assert stl["emission"] is False
        assert window.components_combo.isEnabled()
        assert window.cavities_combo.isEnabled()
        assert window.boolean_combo.isEnabled()
        assert stl["components"] == "main"
        assert stl["cavities"] == "fill"
        assert stl["boolean_fallback"] == "fail"

        # The component-volume threshold only applies to remove-small.
        window._set_value(window.format_combo, "obj")
        window._set_value(window.geometry_combo, "print")
        window._set_value(window.components_combo, "keep")
        assert not window.component_spin.isEnabled()
        window._set_value(window.components_combo, "remove-small")
        assert window.component_spin.isEnabled()

        # Pixel emission data is derived from the mode; there is no extra switch.
        assert not hasattr(window, "emission_check")
        assert window.author_label.text() == "作者：b站@ZZHaccount"
        assert window.author_label.parentWidget().layout().indexOf(window.author_label) >= 0
        assert not window.author_label.isHidden()
    finally:
        window.close()
        settings.clear()
        app.processEvents()
