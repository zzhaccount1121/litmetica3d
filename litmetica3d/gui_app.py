"""Preset-oriented PySide6 interface for litmetica3d v0.5."""

from __future__ import annotations

import multiprocessing as mp
import pathlib
import time

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTabWidget,
    QVBoxLayout, QWidget,
)

from .gui_qt import ConversionWorker, _open_output_folder
from .gui_styles import DARK_STYLE, LIGHT_STYLE
from .output_layout import normalize_output_root

from . import __version__ as VERSION
EXTRA_DARK_STYLE = """
QGroupBox {
    border: 1px solid #2a3547; border-radius: 12px;
    margin-top: 12px; padding: 16px 12px 12px 12px;
    font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 7px; }
QTabWidget::pane { border: 1px solid #2a3547; border-radius: 10px; top: -1px; }
QTabBar::tab { padding: 10px 20px; margin-right: 4px; border-radius: 8px; }
QTabBar::tab:selected { background: #17332f; color: #5de1c0; }
#PresetHero { border: 1px solid #27564f; border-radius: 14px; padding: 16px; }
#PresetButton { text-align: left; min-height: 88px; font-size: 16px; }
#PresetButton:checked { border: 2px solid #48d3b1; background: #16302d; color: #70e8ca; }
"""

EXTRA_LIGHT_STYLE = """
QGroupBox {
    border: 1px solid #ccd7e2; border-radius: 12px;
    margin-top: 12px; padding: 16px 12px 12px 12px;
    font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 7px; }
QTabWidget::pane { border: 1px solid #ccd7e2; border-radius: 10px; top: -1px; }
QTabBar::tab { padding: 10px 20px; margin-right: 4px; border-radius: 8px; }
QTabBar::tab:selected { background: #dff4ee; color: #087f69; }
#PresetHero { border: 1px solid #a9d8cd; border-radius: 14px; padding: 16px; }
#PresetButton { text-align: left; min-height: 88px; font-size: 16px; }
#PresetButton:checked {
    border: 2px solid #159f84;
    background: #dff4ee;
    color: #087f69;
}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Litematica 3D v{VERSION}")
        self.resize(1120, 760)
        self.setMinimumSize(920, 650)
        self.settings = QSettings("litmetica3d", "litmetica3d-v0.5-presets")
        self.dark_theme = self.settings.value("dark_theme", True, bool)
        self.context = mp.get_context("spawn")
        self.cancel_event = self.context.Event()
        self.worker_thread = self.worker = None
        self.started_at = None
        self.current_preset = "print"
        self._applying_preset = False
        self._syncing_constraints = False
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(250)
        self.elapsed_timer.timeout.connect(self._update_elapsed)
        self._build()
        self._connect_advanced_signals()
        saved_output = self.settings.value("output", "")
        if saved_output:
            self.output_edit.setText(str(normalize_output_root(saved_output)))
        self._apply_preset("print", navigate=False)
        self._apply_theme()
        self._show_page(0)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        brand = QLabel("LITEMATICA 3D")
        brand.setObjectName("Brand")
        side.addWidget(brand)
        subtitle = QLabel("SCHEMATIC CONVERTER")
        subtitle.setObjectName("AccentText")
        side.addWidget(subtitle)
        side.addSpacing(26)
        self.nav_buttons = []
        for index, title in enumerate(("预设", "高级", "日志")):
            button = QPushButton(title)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, page=index: self._show_page(page)
            )
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        self.mc_label = QLabel("内置 Minecraft 26.2")
        self.mc_label.setObjectName("Muted")
        self.version_label = QLabel(f"软件版本 {VERSION}")
        self.version_label.setObjectName("Muted")
        self.author_label = QLabel("作者：b站@ZZHaccount")
        self.author_label.setObjectName("Muted")
        self.elapsed_label = QLabel("总耗时 00:00:00")
        self.elapsed_label.setObjectName("Muted")
        side.addWidget(self.mc_label)
        side.addWidget(self.version_label)
        side.addWidget(self.author_label)
        side.addWidget(self.elapsed_label)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("GhostButton")
        self.theme_button.clicked.connect(self._toggle_theme)
        side.addWidget(self.theme_button)
        root.addWidget(sidebar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 22, 28, 24)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        body_layout.addWidget(self.page_title)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._preset_page())
        self.pages.addWidget(self._advanced_page())
        self.pages.addWidget(self._logs_page())
        body_layout.addWidget(self.pages, 1)
        root.addWidget(body, 1)

    def _preset_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        locations = QGroupBox("文件位置")
        grid = QGridLayout(locations)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择一个 .litematic 投影文件")
        choose_input = QPushButton("选择投影")
        choose_input.clicked.connect(self._choose_input)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择总输出位置（自动建立 L3D_output）")
        choose_output = QPushButton("选择路径")
        choose_output.clicked.connect(self._choose_output)
        grid.addWidget(QLabel("投影文件"), 0, 0)
        grid.addWidget(self.input_edit, 0, 1)
        grid.addWidget(choose_input, 0, 2)
        grid.addWidget(QLabel("输出文件夹"), 1, 0)
        grid.addWidget(self.output_edit, 1, 1)
        grid.addWidget(choose_output, 1, 2)
        grid.setColumnStretch(1, 1)
        layout.addWidget(locations)

        hero = QFrame()
        hero.setObjectName("PresetHero")
        hero_box = QVBoxLayout(hero)
        title = QLabel("选择转换预设")
        title.setObjectName("CardTitle")
        help_text = QLabel("预设会填写完整参数；所有选项仍可在“高级”页面修改。")
        help_text.setObjectName("Muted")
        hero_box.addWidget(title)
        hero_box.addWidget(help_text)
        buttons = QHBoxLayout()
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        self.preset_buttons = {}
        presets = (
            ("print", "打印", "STL · 主壳体 · 填充空腔"),
            ("visual", "视觉", "OBJ · 水位 · 材质发光"),
            ("render", "渲染", "OBJ · 原版贴图 · 精确灯光"),
            ("custom", "自定义", "显示当前高级配置"),
        )
        for key, name, description in presets:
            button = QPushButton(f"{name}\n{description}")
            button.setObjectName("PresetButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, preset=key: self._apply_preset(preset)
            )
            self.preset_group.addButton(button)
            self.preset_buttons[key] = button
            buttons.addWidget(button)
        hero_box.addLayout(buttons)
        self.custom_panel = QFrame()
        self.custom_panel.setObjectName("Card")
        custom_box = QVBoxLayout(self.custom_panel)
        custom_title = QLabel("我的配置")
        custom_title.setObjectName("CardTitle")
        self.preset_summary = QPlainTextEdit()
        self.preset_summary.setReadOnly(True)
        self.preset_summary.setFixedHeight(82)
        self.preset_summary.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self.preset_summary.setToolTip("可使用鼠标滚轮查看完整配置")
        custom_box.addWidget(custom_title)
        custom_box.addWidget(self.preset_summary)
        self.custom_panel.setVisible(False)
        hero_box.addWidget(self.custom_panel)
        layout.addWidget(hero)

        task = QGroupBox("转换任务")
        task_box = QVBoxLayout(task)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.status_label = QLabel("等待转换")
        self.status_label.setObjectName("StatusLabel")
        actions = QHBoxLayout()
        actions.addStretch()
        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("DangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        task_box.addWidget(self.progress_bar)
        task_box.addWidget(self.status_label)
        task_box.addLayout(actions)
        layout.addWidget(task)
        layout.addStretch()
        return page

    def _advanced_page(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setSpacing(12)
        basic = QGroupBox("模型与输出")
        grid = QGridLayout(basic)
        self.format_combo = self._combo(("stl", "obj"))
        self.water_combo = self._combo(("cube", "drop", "level"))
        self.fallback_combo = self._combo(("cube", "ignore"))
        self.geometry_combo = self._combo(("print", "visual"))
        self.components_combo = self._combo(("keep", "remove-small", "main"))
        self.cavities_combo = self._combo(("preserve", "fill"))
        self.boolean_combo = self._combo(("voxel32", "fail"))
        fields = (
            ("格式", self.format_combo), ("水体", self.water_combo),
            ("未知方块", self.fallback_combo),
            ("输出用途", self.geometry_combo), ("独立壳体", self.components_combo),
            ("封闭空腔", self.cavities_combo), ("并集失败", self.boolean_combo),
        )
        for i, (label, widget) in enumerate(fields):
            row, column = divmod(i, 2)
            grid.addWidget(QLabel(label), row, column * 2)
            grid.addWidget(widget, row, column * 2 + 1)
        self.solid_textures_check = QCheckBox("**特殊优化：填平贴图镂空**")
        self.solid_textures_check.setStyleSheet("QCheckBox { color: #ef4444; font-weight: bold; }")
        self.solid_textures_check.setToolTip("所有模式可用。不开挖透明像素，保留完整方块或增厚板；贴图颜色和透明度保留。可大幅降低树叶、植物等面数，但改变几何轮廓。默认关闭。")
        grid.addWidget(self.solid_textures_check, 4, 0, 1, 4)
        layout.addWidget(basic)
        numeric = QGroupBox("尺寸与基础选项")
        grid = QGridLayout(numeric)
        self.scale_spin = self._spin(1.0, .0001, 10000, 4)
        self.thickness_spin = self._spin(1 / 16, 1 / 256, 1, 6)
        self.component_spin = self._spin(1 / 4096, 0, 1e9, 12)
        self.center_check = QCheckBox("模型居中")
        self.color_check = QCheckBox("OBJ 基础颜色")
        grid.addWidget(QLabel("比例"), 0, 0)
        grid.addWidget(self.scale_spin, 0, 1)
        grid.addWidget(QLabel("最小厚度（格）"), 0, 2)
        grid.addWidget(self.thickness_spin, 0, 3)
        grid.addWidget(QLabel("最小壳体体积"), 1, 0)
        grid.addWidget(self.component_spin, 1, 1)
        grid.addWidget(self.center_check, 1, 2)
        grid.addWidget(self.color_check, 1, 3)
        layout.addWidget(numeric)

        self.visual_group = QGroupBox("视觉与发光")
        grid = QGridLayout(self.visual_group)
        self.textures_check = QCheckBox("是否带有贴图（由输出用途自动决定）")
        self.seamless_glass_check = QCheckBox("半透明无缝玻璃")
        self.seamless_glass_check.setToolTip("保留玻璃颜色与透明度，移除边框和同色玻璃之间的接触面；关闭时使用原版模型。")
        self.emission_combo = self._combo(
            (("不发光", "none"), ("仅材质发光", "material"),
             ("逐点灯光", "exact"), ("聚类灯光", "clustered"))
        )
        self.emission_strength_spin = self._spin(1, 0, 1000, 2)
        self.emission_config_edit = QLineEdit()
        self.emission_config_edit.setPlaceholderText("可选 JSON 发光规则")
        self.emission_config_button = QPushButton("选择 JSON")
        self.emission_config_button.clicked.connect(self._choose_emission_config)
        grid.addWidget(self.textures_check, 0, 0, 1, 4)
        grid.addWidget(QLabel("发光模式"), 1, 0)
        grid.addWidget(self.emission_combo, 1, 1)
        grid.addWidget(QLabel("发光强度"), 1, 2)
        grid.addWidget(self.emission_strength_spin, 1, 3)
        grid.addWidget(QLabel("发光规则"), 2, 0)
        grid.addWidget(self.emission_config_edit, 2, 1, 1, 2)
        grid.addWidget(self.emission_config_button, 2, 3)
        grid.addWidget(self.seamless_glass_check, 3, 0, 1, 4)
        layout.addWidget(self.visual_group)

        region = QGroupBox("区域")
        row = QHBoxLayout(region)
        self.regions_edit = QLineEdit()
        self.regions_edit.setPlaceholderText(
            "留空为全部；多个区域使用英文逗号分隔"
        )
        row.addWidget(QLabel("转换区域"))
        row.addWidget(self.regions_edit, 1)
        layout.addWidget(region)
        layout.addStretch()

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(content)
        return area

    def _logs_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        self.log_tabs = QTabWidget()
        self.live_log = QPlainTextEdit()
        self.live_log.setReadOnly(True)
        self.live_log.setPlaceholderText("转换过程日志将在这里显示。")
        self.report_log = QPlainTextEdit()
        self.report_log.setReadOnly(True)
        self.report_log.setPlaceholderText(
            "转换完成后，完整报告 JSON 将显示在这里，不写入输出目录。"
        )
        self.log_tabs.addTab(self.live_log, "转换日志")
        self.log_tabs.addTab(self.report_log, "转换报告")
        layout.addWidget(self.log_tabs, 1)
        actions = QHBoxLayout()
        clear = QPushButton("清空当前日志")
        clear.clicked.connect(self._clear_current_log)
        open_output = QPushButton("打开输出文件夹")
        open_output.clicked.connect(self._open_output)
        actions.addWidget(clear)
        actions.addWidget(open_output)
        actions.addStretch()
        layout.addLayout(actions)
        return page

    @staticmethod
    def _combo(values):
        combo = QComboBox()
        for value in values:
            if isinstance(value, tuple):
                label, data = value
            else:
                label = data = value
            combo.addItem(label, data)
        combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        return combo

    @staticmethod
    def _spin(value, minimum, maximum, decimals):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        spin.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        return spin

    @staticmethod
    def _value(combo):
        return combo.currentData()

    @staticmethod
    def _set_value(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_defaults(self):
        self._set_value(self.format_combo, "stl")
        self._set_value(self.water_combo, "cube")
        self._set_value(self.fallback_combo, "cube")
        self._set_value(self.geometry_combo, "print")
        self._set_value(self.components_combo, "keep")
        self._set_value(self.cavities_combo, "preserve")
        self._set_value(self.boolean_combo, "voxel32")
        self._set_value(self.emission_combo, "exact")
        self.scale_spin.setValue(1.0)
        self.thickness_spin.setValue(1 / 16)
        self.component_spin.setValue(1 / 4096)
        self.emission_strength_spin.setValue(1.0)
        self.center_check.setChecked(False)
        self.color_check.setChecked(False)
        self.textures_check.setChecked(True)
        self.seamless_glass_check.setChecked(False)
        self.emission_config_edit.clear()
        self.regions_edit.clear()

    def _apply_preset(self, preset, navigate=True):
        if preset == "custom":
            self.current_preset = "custom"
            self.preset_buttons["custom"].setChecked(True)
            self.custom_panel.setVisible(True)
            self._update_custom_summary()
            if navigate:
                self._show_page(0)
            return

        self._applying_preset = True
        try:
            self._set_defaults()
            if preset == "print":
                values = {
                    "format": "stl", "water": "drop", "fallback": "ignore",
                    "geometry": "print",
                    "components": "main", "cavities": "fill",
                    "emission": "exact",
                }
            elif preset == "visual":
                values = {
                    "format": "obj", "water": "level", "fallback": "ignore",
                    "geometry": "visual",
                    "components": "keep", "cavities": "preserve",
                    "emission": "material",
                }
            else:
                values = {
                    "format": "obj", "water": "drop", "fallback": "ignore",
                    "geometry": "visual",
                    "components": "keep", "cavities": "preserve",
                    "emission": "exact",
                }
            self._set_value(self.format_combo, values["format"])
            self._set_value(self.water_combo, values["water"])
            self._set_value(self.fallback_combo, values["fallback"])
            self._set_value(self.geometry_combo, values["geometry"])
            self._set_value(self.components_combo, values["components"])
            self._set_value(self.cavities_combo, values["cavities"])
            self._set_value(self.emission_combo, values["emission"])
            self.seamless_glass_check.setChecked(preset == "render")
            self.solid_textures_check.setChecked(False)
            self._sync_constraints()
        finally:
            self._applying_preset = False
        self.current_preset = preset
        self.preset_buttons[preset].setChecked(True)
        self.custom_panel.setVisible(False)
        if navigate:
            self._show_page(0)

    def _connect_advanced_signals(self):
        combos = (
            self.format_combo, self.water_combo, self.fallback_combo,
            self.geometry_combo, self.components_combo,
            self.cavities_combo, self.boolean_combo, self.emission_combo,
        )
        spins = (
            self.scale_spin, self.thickness_spin, self.component_spin,
            self.emission_strength_spin,
        )
        checks = (
            self.center_check, self.color_check, self.textures_check,
            self.seamless_glass_check,
            self.solid_textures_check,
        )
        for combo in combos:
            combo.currentIndexChanged.connect(self._advanced_changed)
        for spin in spins:
            spin.valueChanged.connect(self._advanced_changed)
        for check in checks:
            check.toggled.connect(self._advanced_changed)
        self.emission_config_edit.textChanged.connect(self._advanced_changed)
        self.regions_edit.textChanged.connect(self._advanced_changed)

    def _sync_constraints(self):
        if self._syncing_constraints:
            return
        self._syncing_constraints = True
        try:
            is_stl = self._value(self.format_combo) == "stl"
            if is_stl:
                self._set_value(self.geometry_combo, "print")
            self.geometry_combo.setEnabled(not is_stl)
            self.geometry_combo.setToolTip(
                "STL 只支持打印用途，已锁定为 print。" if is_stl else ""
            )

            is_print = self._value(self.geometry_combo) == "print"
            is_visual = not is_stl and not is_print
            self.visual_group.setEnabled(is_visual)
            self.seamless_glass_check.setEnabled(is_visual)
            self.visual_group.setToolTip(
                "视觉贴图与发光仅在 OBJ + visual 时可用。"
                if not is_visual else ""
            )
            self.color_check.setEnabled(is_visual)
            self.textures_check.setChecked(is_visual)
            self.textures_check.setEnabled(False)
            self.textures_check.setToolTip(
                "视觉用途固定带有原版贴图；打印用途固定不带贴图。"
            )

            emission_active = (
                is_visual and self._value(self.emission_combo) != "none"
            )
            self.emission_combo.setEnabled(is_visual)
            self.emission_strength_spin.setEnabled(emission_active)
            self.emission_config_edit.setEnabled(emission_active)
            self.emission_config_button.setEnabled(emission_active)

            for widget in (
                self.components_combo, self.cavities_combo,
                self.boolean_combo,
            ):
                widget.setEnabled(is_print)
                widget.setToolTip("仅打印模式可用。" if not is_print else "")
            self.component_spin.setEnabled(
                is_print and self._value(self.components_combo) == "remove-small"
            )
        finally:
            self._syncing_constraints = False

    def _advanced_changed(self, *_args):
        if self._applying_preset or self._syncing_constraints:
            return
        self._sync_constraints()
        self.current_preset = "custom"
        self.preset_buttons["custom"].setChecked(True)
        self.custom_panel.setVisible(True)
        self._update_custom_summary()

    def _update_custom_summary(self):
        options = self._snapshot()
        options["regions"] = options["regions"] or "全部"
        options["emission_config"] = options["emission_config"] or "默认"
        self.preset_summary.setPlainText(
            "输出：{format}  |  水体：{water}  |  未知方块：{fallback}  |  "
            "自动优化  |  特殊优化（填平镂空）：{solid_textures}\n"
            "用途：{geometry}  |  独立壳体：{components}  |  "
            "空腔：{cavities}  |  并集失败：{boolean_fallback}\n"
            "比例：{scale:g}  |  最小厚度：{thickness:g}  |  "
            "最小壳体体积：{min_component_volume:g}  |  居中：{center}\n"
            "OBJ颜色：{color}  |  是否带有贴图：{textures}  |  "
            "无缝玻璃：{seamless_glass}  |  发光模式：{blender_lights}  |  "
            "强度：{emission_strength:g}\n"
            "区域：{regions}  |  发光规则：{emission_config}"
            .format(**options)
        )
    def _show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(("预设与转换", "高级选项", "日志与报告")[index])
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def _toggle_theme(self):
        self.dark_theme = not self.dark_theme
        self.settings.setValue("dark_theme", self.dark_theme)
        self._apply_theme()

    def _apply_theme(self):
        base = DARK_STYLE if self.dark_theme else LIGHT_STYLE
        extra = EXTRA_DARK_STYLE if self.dark_theme else EXTRA_LIGHT_STYLE
        QApplication.instance().setStyleSheet(base + extra)
        self.theme_button.setText(
            "切换浅色" if self.dark_theme else "切换暗色"
        )
    def _choose_input(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择 Litematic 文件", "", "Litematic (*.litematic)"
        )
        if not file:
            return
        self.input_edit.setText(file)
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(normalize_output_root(pathlib.Path(file).parent)))
        try:
            from .litematic import load_schematic
            schematic = load_schematic(file)
            self.regions_edit.setText(", ".join(schematic.regions))
            self._log(f"已读取区域：{', '.join(schematic.regions)}")
        except Exception as exc:
            self._log(f"读取区域失败：{exc}")

    def _choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出位置（自动建立 L3D_output）")
        if folder:
            output_root = str(normalize_output_root(folder))
            self.output_edit.setText(output_root)
            self.settings.setValue("output", output_root)

    def _choose_emission_config(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择发光规则", "", "JSON (*.json);;所有文件 (*.*)"
        )
        if file:
            self.emission_config_edit.setText(file)

    def _open_output(self):
        _open_output_folder(self, self.output_edit.text())

    def _clear_current_log(self):
        current = self.log_tabs.currentWidget()
        current.clear()

    def _snapshot(self):
        output_format = self._value(self.format_combo)
        geometry = self._value(self.geometry_combo)
        if output_format == "stl":
            geometry = "print"

        is_visual = output_format == "obj" and geometry == "visual"
        has_textures = is_visual
        emission_mode = self._value(self.emission_combo)
        has_emission = has_textures and emission_mode != "none"
        components = self._value(self.components_combo) if not is_visual else "keep"
        cavities = self._value(self.cavities_combo) if not is_visual else "preserve"
        boolean_fallback = self._value(self.boolean_combo) if not is_visual else "voxel32"
        min_component_volume = (
            self.component_spin.value()
            if components == "remove-small"
            else 1 / 4096
        )

        return {
            "format": output_format,
            "water": self._value(self.water_combo),
            "fallback": self._value(self.fallback_combo),
            "optimize": "safe",
            "thickness": self.thickness_spin.value(),
            "scale": self.scale_spin.value(),
            "center": self.center_check.isChecked(),
            "color": is_visual and self.color_check.isChecked(),
            "textures": has_textures,
            "seamless_glass": is_visual and self.seamless_glass_check.isChecked(),
            "solid_textures": self.solid_textures_check.isChecked(),
            "emission": has_emission,
            "emission_strength": (
                self.emission_strength_spin.value() if has_emission else 1.0
            ),
            "blender_lights": emission_mode if has_emission else "none",
            "emission_config": (
                self.emission_config_edit.text().strip() if has_emission else ""
            ),
            "regions": self.regions_edit.text().strip(),
            "geometry": geometry,
            "components": components,
            "cavities": cavities,
            "boolean_fallback": boolean_fallback,
            "min_component_volume": min_component_volume,
        }

    def _start(self):
        source = pathlib.Path(self.input_edit.text().strip())
        selected = self.output_edit.text().strip()
        output = str(normalize_output_root(selected)) if selected else ""
        if not source.is_file() or source.suffix.lower() != ".litematic":
            QMessageBox.warning(self, "投影文件", "请选择一个有效的 .litematic 文件。")
            return
        if not output:
            QMessageBox.warning(self, "输出位置", "请选择输出文件夹。")
            return
        self.output_edit.setText(output)
        self.settings.setValue("output", output)
        self.cancel_event.clear()
        self.progress_bar.setValue(0)
        self.report_log.clear()
        self._set_running(True)
        self.started_at = time.monotonic()
        self.elapsed_timer.start()
        self.status_label.setText("正在启动转换进程……")
        self._log("—" * 52)
        self._log(f"开始转换：{source.name}；预设：{self.current_preset}")

        self.worker_thread = QThread(self)
        self.worker = ConversionWorker(
            [source], output, self._snapshot(), self.context, self.cancel_event
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.log.connect(self._log)
        self.worker.report_ready.connect(self._show_report)
        self.worker.completed.connect(self._complete)
        self.worker.cancelled.connect(self._cancelled)
        self.worker.failed.connect(self._failed)
        for signal in (
            self.worker.completed, self.worker.cancelled, self.worker.failed,
        ):
            signal.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self._thread_done)
        self.worker_thread.start()

    @Slot(float, str, str)
    def _progress(self, value, text, stage):
        self.progress_bar.setValue(max(0, min(1000, round(value * 10))))
        self.status_label.setText(f"{stage} · {text}")

    @Slot(str)
    def _show_report(self, report_json):
        self.report_log.setPlainText(report_json)

    @Slot(str)
    def _complete(self, text):
        self.progress_bar.setValue(1000)
        self.status_label.setText(text)
        self._log(text)
        self._finish_task()
        self._show_page(2)
        self.log_tabs.setCurrentWidget(self.report_log)

    @Slot(str)
    def _cancelled(self, text):
        self.status_label.setText(text)
        self._log(text)
        self._finish_task()

    @Slot(str)
    def _failed(self, text):
        self.status_label.setText("转换失败")
        self._log(f"错误：{text}")
        self._finish_task()
        self._show_page(2)
        QMessageBox.critical(self, "转换失败", text)

    def _finish_task(self):
        self.elapsed_timer.stop()
        self._update_elapsed()
        self._set_running(False)

    @Slot()
    def _thread_done(self):
        if self.worker_thread:
            self.worker_thread.deleteLater()
        self.worker_thread = self.worker = None

    def _cancel(self):
        if self.worker_thread and self.worker_thread.isRunning():
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在取消……")
            self._log("已请求取消，正在结束转换进程……")

    def _set_running(self, running):
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.input_edit.setEnabled(not running)
        self.output_edit.setEnabled(not running)

    def _update_elapsed(self):
        elapsed = 0 if self.started_at is None else int(
            time.monotonic() - self.started_at
        )
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_label.setText(
            f"总耗时 {hours:02d}:{minutes:02d}:{seconds:02d}"
        )

    def _log(self, text):
        self.live_log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")

    def closeEvent(self, event: QCloseEvent):
        if self.worker_thread and self.worker_thread.isRunning():
            answer = QMessageBox.question(
                self, "转换仍在进行", "关闭会终止当前转换，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_event.set()
            self.worker_thread.quit()
            self.worker_thread.wait(2500)
        event.accept()


def launch_gui():
    mp.freeze_support()
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("litmetica3d")
    app.setApplicationName("litmetica3d-v0.5")
    app.setApplicationVersion(VERSION)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(launch_gui())
