"""Modern PySide6 desktop interface for litmetica3d v0.5."""

from __future__ import annotations

from dataclasses import asdict
import json
import multiprocessing as mp
import pathlib
import queue
import time

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .gui_styles import DARK_STYLE, LIGHT_STYLE
from .output_layout import next_model_path, normalize_output_root

from . import __version__ as VERSION


def _open_output_folder(parent, selected):
    """Open an existing output directory without shell or platform assumptions."""
    selected = selected.strip()
    try:
        folder = normalize_output_root(selected) if selected else None
        if folder is None or not folder.is_dir():
            QMessageBox.information(parent, "输出位置", "输出文件夹不存在。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            QMessageBox.warning(parent, "输出位置", "无法打开输出文件夹。")
    except (OSError, RuntimeError, ValueError) as exc:
        QMessageBox.warning(parent, "输出位置", f"无法打开输出文件夹：{exc}")


class GUIConversionCancelled(Exception):
    pass


def _conversion_process(options_data, events, cancel_event):
    from .conversion import ConversionCancelled, ConversionOptions, convert
    try:
        report = convert(
            ConversionOptions(**options_data),
            lambda stage, value, text: events.put(
                ("progress", stage, value, text)
            ),
            cancel_event.is_set,
        )
        events.put(("result", report))
    except ConversionCancelled:
        events.put(("cancelled",))
    except Exception as exc:
        events.put(("error", str(exc)))


class ConversionWorker(QObject):
    progress = Signal(float, str, str)
    log = Signal(str)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal(str)
    report_ready = Signal(str)

    def __init__(self, files, output_dir, options, context, cancel_event):
        super().__init__()
        self.files = [pathlib.Path(p) for p in files]
        self.output_dir = normalize_output_root(output_dir)
        self.options = options
        self.context = context
        self.cancel_event = cancel_event

    @Slot()
    def run(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            regions = tuple(x.strip() for x in self.options["regions"].split(",")
                            if x.strip())
            for index, source in enumerate(self.files):
                if self.cancel_event.is_set():
                    raise GUIConversionCancelled()
                fmt = self.options["format"]
                data = {
                    "input_path": source,
                    "output_path": next_model_path(self.output_dir, source, fmt),
                    "output_format": fmt,
                    "water": self.options["water"],
                    "fallback": self.options["fallback"],
                    "optimize": self.options["optimize"],
                    "minimum_thickness": self.options["thickness"],
                    "scale": self.options["scale"],
                    "center": self.options["center"],
                    "color": self.options["color"],
                    "textures": self.options["textures"],
                    "seamless_glass": self.options.get("seamless_glass", False),
                    "solid_textures": self.options.get("solid_textures", False),
                    "emission": self.options["emission"],
                    "emission_strength": self.options["emission_strength"],
                    "blender_lights": self.options["blender_lights"],
                    "emission_config": (
                        pathlib.Path(self.options["emission_config"])
                        if self.options["emission_config"] else None
                    ),
                    "regions": regions if len(self.files) == 1 else (),
                    "geometry": self.options["geometry"],
                    "components": self.options["components"],
                    "cavities": self.options["cavities"],
                    "boolean_fallback": self.options["boolean_fallback"],
                    "min_component_volume": self.options["min_component_volume"],
                    "save_report": False,
                }
                self.log.emit(f"[{index + 1}/{len(self.files)}] 开始：{source.name}")
                report = self._run_one(data, index, len(self.files))
                self.report_ready.emit(json.dumps(
                    asdict(report), ensure_ascii=False, indent=2
                ))
                self.log.emit(
                    f"完成：{report.triangles} 个三角形，"
                    f"回落 {report.fallback_cubes}，忽略 {report.ignored}"
                )
                if report.geometry_mode == "visual":
                    self.log.emit(
                        f"视觉：删除透明像素 {report.transparent_pixels_removed}，"
                        f"染色贴图 {report.tinted_textures}，"
                        f"发光方块 {report.emissive_blocks}"
                    )
                if report.solid:
                    self.log.emit(
                        f"打印检查：{'通过' if report.solid.printable else '失败'}，"
                        f"壳体 {report.solid.component_count}，"
                        f"空腔 {report.solid.cavity_count}"
                    )
            self.completed.emit("全部转换完成")
        except GUIConversionCancelled:
            self.cancelled.emit("转换已取消")
        except Exception as exc:
            self.failed.emit(str(exc))

    def _run_one(self, data, file_index, file_count):
        events = self.context.Queue()
        process = self.context.Process(
            target=_conversion_process,
            args=(data, events, self.cancel_event), daemon=True,
        )
        process.start()
        result = error = None
        cancelled_at = None
        try:
            while True:
                if self.cancel_event.is_set() and cancelled_at is None:
                    cancelled_at = time.monotonic()
                if (cancelled_at is not None and process.is_alive()
                        and time.monotonic() - cancelled_at > 2):
                    process.terminate()
                    process.join(2)
                    raise GUIConversionCancelled()
                try:
                    kind, *payload = events.get(timeout=.1)
                except queue.Empty:
                    if not process.is_alive():
                        break
                    continue
                if kind == "progress":
                    stage, value, text = payload
                    overall = ((file_index + value) / file_count) * 100
                    self.progress.emit(overall, text, stage)
                elif kind == "result":
                    result = payload[0]
                    break
                elif kind == "cancelled":
                    raise GUIConversionCancelled()
                elif kind == "error":
                    error = payload[0]
                    break
        finally:
            if process.is_alive():
                process.join(1)
            if process.is_alive():
                process.terminate()
                process.join(2)
            events.close()
        if error:
            raise RuntimeError(error)
        if result is None:
            if self.cancel_event.is_set():
                raise GUIConversionCancelled()
            raise RuntimeError(f"转换进程异常退出（退出码 {process.exitcode}）")
        return result



class DropList(QListWidget):
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(160)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if any(u.toLocalFile().lower().endswith(".litematic")
               for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.toLocalFile().lower().endswith(".litematic")]
        if files:
            self.dropped.emit(files)
            event.acceptProposedAction()


class Card(QFrame):
    def __init__(self, title, subtitle=""):
        super().__init__()
        self.setObjectName("Card")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(20, 18, 20, 20)
        self.box.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        self.box.addWidget(heading)
        if subtitle:
            text = QLabel(subtitle)
            text.setObjectName("Muted")
            text.setWordWrap(True)
            self.box.addWidget(text)


class MainWindow(QMainWindow):
    PAGE_INFO = (
        ("项目", "选择投影、输出位置与转换预设"),
        ("转换设置", "设置输出用途、格式和基础几何行为"),
        ("高级选项", "控制壳体、空腔、区域与布尔回退"),
        ("任务与日志", "查看进度、耗时和转换结果"),
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Litematica 3D v{VERSION}")
        self.resize(1280, 820)
        self.setMinimumSize(1040, 700)
        self.settings = QSettings("litmetica3d", "litmetica3d-v0.5")
        self.dark_theme = self.settings.value("dark_theme", True, bool)
        self.files = []
        self.context = mp.get_context("spawn")
        self.cancel_event = self.context.Event()
        self.worker_thread = self.worker = None
        self.geometry = "print"
        self._syncing_constraints = False
        self._build()
        self._restore()
        self._apply_theme()
        self._page(0)
        self._sync_visual()

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(226)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        brand = QLabel("LITEMATICA 3D")
        brand.setObjectName("Brand")
        side.addWidget(brand)
        ver = QLabel(f"DESKTOP  ·  v{VERSION}")
        ver.setObjectName("AccentText")
        side.addWidget(ver)
        side.addSpacing(24)
        self.nav = []
        for i, (title, _) in enumerate(self.PAGE_INFO):
            button = QPushButton(title)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, n=i: self._page(n))
            side.addWidget(button)
            self.nav.append(button)
        side.addStretch()
        resource = QFrame()
        resource.setObjectName("ResourcePanel")
        rbox = QVBoxLayout(resource)
        rbox.addWidget(QLabel("Minecraft 26.2"))
        muted = QLabel("内置 1198 个方块状态文件\n无需本地 Minecraft")
        muted.setObjectName("Muted")
        rbox.addWidget(muted)
        side.addWidget(resource)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("GhostButton")
        self.theme_button.clicked.connect(self._toggle_theme)
        side.addWidget(self.theme_button)
        root.addWidget(sidebar)

        body = QWidget()
        body_box = QVBoxLayout(body)
        body_box.setContentsMargins(30, 24, 30, 20)
        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("Muted")
        body_box.addWidget(self.page_title)
        body_box.addWidget(self.page_subtitle)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._project_page())
        self.pages.addWidget(self._settings_page())
        self.pages.addWidget(self._advanced_page())
        self.pages.addWidget(self._activity_page())
        body_box.addWidget(self.pages, 1)
        body_box.addWidget(self._bottom_bar())
        root.addWidget(body, 1)

    def _scroll(self, layout):
        widget = QWidget()
        widget.setLayout(layout)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(widget)
        return area

    def _project_page(self):
        layout = QVBoxLayout()
        layout.setSpacing(16)
        hero = QFrame()
        hero.setObjectName("Hero")
        hero_box = QHBoxLayout(hero)
        hero_box.setContentsMargins(24, 22, 24, 22)
        text_box = QVBoxLayout()
        title = QLabel("把 Minecraft 投影带到现实")
        title.setObjectName("HeroTitle")
        desc = QLabel(
            "打印模式生成封闭实体；视觉模式保留原版贴图、"
            "透明裁切与 Blender 发光数据。"
        )
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        text_box.addWidget(title)
        text_box.addWidget(desc)
        hero_box.addLayout(text_box, 1)
        badge = QLabel("内置 26.2 资源\n无需本地 Minecraft")
        badge.setObjectName("HeroBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_box.addWidget(badge)
        layout.addWidget(hero)

        card = Card("投影文件", "拖入一个或多个 .litematic 文件，可批量转换。")
        self.file_list = DropList()
        self.file_list.dropped.connect(self._add_files)
        card.box.addWidget(self.file_list)
        actions = QHBoxLayout()
        choose = QPushButton("选择文件")
        choose.setObjectName("SecondaryButton")
        choose.clicked.connect(self._choose_files)
        remove = QPushButton("移除所选")
        remove.clicked.connect(self._remove_files)
        clear = QPushButton("清空")
        clear.clicked.connect(self._clear_files)
        actions.addWidget(choose)
        actions.addWidget(remove)
        actions.addWidget(clear)
        actions.addStretch()
        card.box.addLayout(actions)
        layout.addWidget(card)

        output = Card("输出位置", "模型统一保存在 L3D_output/投影名/ 子文件夹。")
        row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("请选择总输出位置（自动建立 L3D_output）")
        browse = QPushButton("选择文件夹")
        browse.clicked.connect(self._choose_output)
        row.addWidget(self.output_edit, 1)
        row.addWidget(browse)
        output.box.addLayout(row)
        layout.addWidget(output)

        presets = Card("快速预设", "预设只调整参数，仍可逐项修改。")
        row = QHBoxLayout()
        for title, subtitle, name in (
            ("3D 打印", "封闭实体 · STL", "print"),
            ("Blender 视觉", "原版贴图 · 发光", "visual"),
            ("大型场景", "低内存 · 聚类灯光", "large"),
        ):
            button = QPushButton(f"{title}\n{subtitle}")
            button.setObjectName("PresetButton")
            button.setMinimumHeight(62)
            button.clicked.connect(
                lambda _=False, preset=name: self._preset(preset)
            )
            row.addWidget(button)
        presets.box.addLayout(row)
        layout.addWidget(presets)
        layout.addStretch()
        return self._scroll(layout)


    def _settings_page(self):
        layout = QVBoxLayout()
        layout.setSpacing(16)
        purpose = Card(
            "输出用途",
            "打印模式强调封闭无破面；视觉模式强调贴图和渲染效果。",
        )
        row = QHBoxLayout()
        self.print_mode = QPushButton(
            "3D 打印模式\n封闭实体、布尔并集、可打印性检查"
        )
        self.visual_mode = QPushButton(
            "视觉渲染模式\n原版贴图、透明裁切、Blender 发光"
        )
        for button in (self.print_mode, self.visual_mode):
            button.setObjectName("ModeButton")
            button.setCheckable(True)
            button.setMinimumHeight(72)
            row.addWidget(button)
        self.print_mode.clicked.connect(lambda: self._set_geometry("print"))
        self.visual_mode.clicked.connect(lambda: self._set_geometry("visual"))
        purpose.box.addLayout(row)
        layout.addWidget(purpose)

        basic = Card("基础设置")
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(12)
        self.format_combo = self._combo((("STL", "stl"), ("OBJ", "obj")))
        self.water_combo = self._combo((
            ("完整方块", "cube"), ("忽略水", "drop"), ("水位高度", "level"),
        ))
        self.fallback_combo = self._combo((
            ("回落成立方体", "cube"), ("忽略", "ignore"),
        ))
        self.scale_spin = self._spin(1.0, .0001, 10000, 4)
        self.thickness_spin = self._spin(1 / 16, 1 / 256, 1, 6)
        fields = (
            ("输出格式", self.format_combo),
            ("水体处理", self.water_combo),
            ("未知方块", self.fallback_combo),
            ("模型比例", self.scale_spin),
            ("最小实体厚度（格）", self.thickness_spin),
        )
        for i, (label, widget) in enumerate(fields):
            x, y = (i % 2) * 2, i // 2
            grid.addWidget(self._label(label), y, x)
            grid.addWidget(widget, y, x + 1)
        self.center_check = QCheckBox("模型居中")
        self.color_check = QCheckBox("OBJ 基础颜色")
        grid.addWidget(self.center_check, 3, 0, 1, 2)
        grid.addWidget(self.color_check, 3, 2, 1, 2)
        basic.box.addLayout(grid)
        layout.addWidget(basic)

        self.visual_card = Card(
            "视觉与发光",
            "视觉用途仅支持 OBJ，写入贴图和 Blender 辅助文件。",
        )
        vgrid = QGridLayout()
        self.textures_check = QCheckBox("使用原版贴图")
        self.emission_check = QCheckBox("生成像素级发光数据")
        self.emission_combo = self._combo((
            ("不发光", "none"), ("仅材质发光", "material"),
            ("精确灯光", "exact"), ("聚类灯光", "clustered"),
        ))
        self.emission_strength_spin = self._spin(1, 0, 1000, 2)
        vgrid.addWidget(self.textures_check, 0, 0, 1, 2)
        vgrid.addWidget(self.emission_check, 0, 2, 1, 2)
        vgrid.addWidget(self._label("Blender 发光模式"), 1, 0)
        vgrid.addWidget(self.emission_combo, 1, 1)
        vgrid.addWidget(self._label("发光强度倍率"), 1, 2)
        vgrid.addWidget(self.emission_strength_spin, 1, 3)
        self.visual_card.box.addLayout(vgrid)
        layout.addWidget(self.visual_card)
        layout.addStretch()
        self.format_combo.currentIndexChanged.connect(self._sync_visual)
        self.emission_combo.currentIndexChanged.connect(self._sync_visual)
        self.emission_check.toggled.connect(self._sync_visual)
        self.textures_check.toggled.connect(self._sync_visual)
        return self._scroll(layout)

    def _advanced_page(self):
        layout = QVBoxLayout()
        layout.setSpacing(16)
        topology = Card(
            "实体与拓扑",
            "独立壳体和空腔策略仅影响打印流程。",
        )
        grid = QGridLayout()
        self.components_combo = self._combo((
            ("全部保留并报告", "keep"),
            ("删除较小壳体", "remove-small"),
            ("仅保留主要壳体", "main"),
        ))
        self.cavities_combo = self._combo((
            ("保留空腔", "preserve"), ("填充空腔", "fill"),
        ))
        self.boolean_combo = self._combo((
            ("局部体素 32 回退", "voxel32"), ("失败并停止", "fail"),
        ))
        self.component_spin = self._spin(1 / 4096, 0, 1e9, 12)
        self.components_combo.currentIndexChanged.connect(self._sync_visual)
        for i, (label, widget) in enumerate((
            ("独立壳体策略", self.components_combo),
            ("封闭空腔策略", self.cavities_combo),
            ("布尔失败处理", self.boolean_combo),
            ("最小壳体体积", self.component_spin),
        )):
            grid.addWidget(self._label(label), i, 0)
            grid.addWidget(widget, i, 1)
        grid.setColumnStretch(1, 1)
        topology.box.addLayout(grid)
        layout.addWidget(topology)

        selection = Card("区域与发光规则")
        self.regions_edit = QLineEdit()
        self.regions_edit.setPlaceholderText(
            "留空表示全部区域；多个区域用英文逗号分隔"
        )
        self.emission_config_edit = QLineEdit()
        self.emission_config_edit.setPlaceholderText(
            "可选：自定义 .json 发光规则"
        )
        self.emission_config_button = QPushButton("选择 JSON")
        self.emission_config_button.clicked.connect(self._choose_emission_config)
        grid = QGridLayout()
        grid.addWidget(self._label("转换区域"), 0, 0)
        grid.addWidget(self.regions_edit, 0, 1, 1, 2)
        grid.addWidget(self._label("发光规则"), 1, 0)
        grid.addWidget(self.emission_config_edit, 1, 1)
        grid.addWidget(self.emission_config_button, 1, 2)
        grid.setColumnStretch(1, 1)
        selection.box.addLayout(grid)
        layout.addWidget(selection)

        note = Card("模式说明")
        text = QLabel(
            "打印模式用于 3D 打印，执行封闭实体、精确并集和拓扑检查，"
            "目标是无开放边、无破面。\n\n"
            "视觉模式用于 Blender 等渲染软件，保留彩色贴图、透明像素"
            "与发光信息；为了忠实外观，局部可能不是可打印流形。"
        )
        text.setWordWrap(True)
        note.box.addWidget(text)
        layout.addWidget(note)
        layout.addStretch()
        return self._scroll(layout)

    def _activity_page(self):
        layout = QVBoxLayout()
        status = Card("任务状态")
        self.activity_title = QLabel("等待转换")
        self.activity_title.setObjectName("ActivityTitle")
        self.activity_detail = QLabel("选择投影和输出目录后即可开始。")
        self.activity_detail.setObjectName("Muted")
        self.activity_detail.setWordWrap(True)
        status.box.addWidget(self.activity_title)
        status.box.addWidget(self.activity_detail)
        layout.addWidget(status)
        log_card = Card("运行日志")
        actions = QHBoxLayout()
        clear = QPushButton("清空日志")
        clear.clicked.connect(self._clear_log)
        open_button = QPushButton("打开输出文件夹")
        open_button.clicked.connect(self._open_output)
        actions.addWidget(clear)
        actions.addWidget(open_button)
        actions.addStretch()
        log_card.box.addLayout(actions)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(320)
        log_card.box.addWidget(self.log_edit)
        layout.addWidget(log_card, 1)
        return self._scroll(layout)

    def _bottom_bar(self):
        bar = QFrame()
        bar.setObjectName("BottomBar")
        layout = QHBoxLayout(bar)
        progress = QVBoxLayout()
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("StatusLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        progress.addWidget(self.status_label)
        progress.addWidget(self.progress_bar)
        layout.addLayout(progress, 1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("DangerButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._start)
        layout.addWidget(self.cancel_button)
        layout.addWidget(self.start_button)
        return bar

    @staticmethod
    def _combo(items):
        combo = QComboBox()
        for label, value in items:
            combo.addItem(label, value)
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
        spin.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        return spin

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    @staticmethod
    def _value(combo):
        return combo.currentData()

    @staticmethod
    def _set_value(combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _page(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.PAGE_INFO[index][0])
        self.page_subtitle.setText(self.PAGE_INFO[index][1])
        for i, button in enumerate(self.nav):
            button.setChecked(i == index)

    def _toggle_theme(self):
        self.dark_theme = not self.dark_theme
        self.settings.setValue("dark_theme", self.dark_theme)
        self._apply_theme()

    def _apply_theme(self):
        QApplication.instance().setStyleSheet(
            DARK_STYLE if self.dark_theme else LIGHT_STYLE
        )
        self.theme_button.setText(
            "切换到浅色界面" if self.dark_theme else "切换到暗色界面"
        )

    def _set_geometry(self, value):
        self.geometry = value
        self._sync_visual()

    def _sync_visual(self):
        if self._syncing_constraints:
            return
        self._syncing_constraints = True
        try:
            is_stl = self._value(self.format_combo) == "stl"
            if is_stl or self.geometry not in {"print", "visual"}:
                self.geometry = "print"
            is_print = self.geometry == "print"
            is_visual = not is_stl and not is_print
            self.print_mode.setChecked(is_print)
            self.visual_mode.setChecked(is_visual)
            self.visual_mode.setEnabled(not is_stl)
            self.visual_mode.setToolTip("STL 只支持打印用途。" if is_stl else "")
            self.visual_card.setEnabled(is_visual)
            self.color_check.setEnabled(is_visual)
            self.textures_check.setChecked(is_visual)
            self.textures_check.setEnabled(False)
            self.textures_check.setToolTip("贴图由输出用途自动决定。")
            emission_available = is_visual and self._value(self.emission_combo) != "none"
            emission_active = emission_available and self.emission_check.isChecked()
            self.emission_check.setEnabled(emission_available)
            self.emission_combo.setEnabled(is_visual)
            self.emission_strength_spin.setEnabled(emission_active)
            self.emission_config_edit.setEnabled(emission_active)
            self.emission_config_button.setEnabled(emission_active)
            for widget in (self.components_combo, self.cavities_combo, self.boolean_combo):
                widget.setEnabled(is_print)
                widget.setToolTip("仅打印模式可用。" if not is_print else "")
            self.component_spin.setEnabled(
                is_print and self._value(self.components_combo) == "remove-small"
            )
        finally:
            self._syncing_constraints = False

    def _preset(self, name):
        if name == "print":
            values = ("print", "stl", "cube", "none")
        elif name == "visual":
            values = ("visual", "obj", "level", "exact")
        else:
            values = ("visual", "obj", "drop", "clustered")
        self.geometry = values[0]
        self._set_value(self.format_combo, values[1])
        self._set_value(self.water_combo, values[2])
        self._set_value(self.emission_combo, values[3])
        if name != "print":
            self.textures_check.setChecked(True)
            self.emission_check.setChecked(True)
        self._sync_visual()
        self._page(1)

    def _choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 Litematic 文件", "", "Litematic (*.litematic)"
        )
        self._add_files(files)

    def _add_files(self, files):
        known = {str(p).lower() for p in self.files}
        for name in files:
            path = pathlib.Path(name)
            if str(path).lower() not in known:
                self.files.append(path)
                known.add(str(path).lower())
        self._refresh_files()
        if self.files and not self.output_edit.text():
            self.output_edit.setText(str(normalize_output_root(self.files[0].parent)))
        if self.files:
            try:
                from .litematic import load_schematic
                schematic = load_schematic(str(self.files[0]))
                self.regions_edit.setText(", ".join(schematic.regions))
                self._log(f"已读取区域：{', '.join(schematic.regions)}")
            except Exception as exc:
                self._log(f"读取区域失败：{exc}")

    def _refresh_files(self):
        self.file_list.clear()
        for path in self.files:
            self.file_list.addItem(f"{path.name}    ·    {path.parent}")

    def _remove_files(self):
        rows = sorted(
            {index.row() for index in self.file_list.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.files.pop(row)
        self._refresh_files()

    def _clear_files(self):
        self.files.clear()
        self.file_list.clear()
        self.regions_edit.clear()

    def _choose_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出位置（自动建立 L3D_output）")
        if folder:
            self.output_edit.setText(str(normalize_output_root(folder)))

    def _choose_emission_config(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择发光规则", "", "JSON (*.json);;所有文件 (*.*)"
        )
        if file:
            self.emission_config_edit.setText(file)

    def _open_output(self):
        _open_output_folder(self, self.output_edit.text())

    def _clear_log(self):
        self.log_edit.clear()

    def _snapshot(self):
        output_format = self._value(self.format_combo)
        geometry = self.geometry if output_format == "obj" else "print"
        if geometry not in {"print", "visual"}:
            geometry = "print"
        is_visual = output_format == "obj" and geometry == "visual"
        emission_mode = self._value(self.emission_combo)
        has_emission = is_visual and self.emission_check.isChecked() and emission_mode != "none"
        components = self._value(self.components_combo) if not is_visual else "keep"
        return {
            "format": output_format,
            "water": self._value(self.water_combo),
            "fallback": self._value(self.fallback_combo),
            "optimize": "safe",
            "thickness": self.thickness_spin.value(),
            "scale": self.scale_spin.value(),
            "center": self.center_check.isChecked(),
            "color": is_visual and self.color_check.isChecked(),
            "textures": is_visual,
            "emission": has_emission,
            "emission_strength": self.emission_strength_spin.value() if has_emission else 1.0,
            "blender_lights": emission_mode if has_emission else "none",
            "emission_config": self.emission_config_edit.text().strip() if has_emission else "",
            "regions": self.regions_edit.text().strip(),
            "geometry": geometry,
            "components": components,
            "cavities": self._value(self.cavities_combo) if not is_visual else "preserve",
            "boolean_fallback": self._value(self.boolean_combo) if not is_visual else "voxel32",
            "min_component_volume": self.component_spin.value() if components == "remove-small" else 1 / 4096,
        }

    def _start(self):
        if not self.files:
            QMessageBox.warning(self, "缺少投影", "请先选择投影文件。")
            return
        selected = self.output_edit.text().strip()
        output = str(normalize_output_root(selected)) if selected else ""
        if not output:
            QMessageBox.warning(self, "缺少路径", "请选择输出文件夹。")
            return
        self.output_edit.setText(output)
        self._save()
        self.cancel_event.clear()
        self.progress_bar.setValue(0)
        self._running(True)
        self.activity_title.setText("转换进行中")
        self.activity_detail.setText("正在启动独立转换进程……")
        self._page(3)
        self._log("—" * 50)
        self._log(f"任务开始，共 {len(self.files)} 个文件")

        self.worker_thread = QThread(self)
        self.worker = ConversionWorker(
            self.files, output, self._snapshot(),
            self.context, self.cancel_event,
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._progress)
        self.worker.log.connect(self._log)
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
        self.status_label.setText(text)
        self.activity_detail.setText(f"{stage} · {text}")

    @Slot(str)
    def _complete(self, text):
        self.progress_bar.setValue(1000)
        self.status_label.setText(text)
        self.activity_title.setText("转换完成")
        self.activity_detail.setText("模型和报告已写入输出文件夹。")
        self._log(text)
        self._running(False)

    @Slot(str)
    def _cancelled(self, text):
        self.status_label.setText(text)
        self.activity_title.setText("任务已取消")
        self.activity_detail.setText("独立转换进程已停止。")
        self._log(text)
        self._running(False)

    @Slot(str)
    def _failed(self, text):
        self.status_label.setText("转换失败")
        self.activity_title.setText("转换失败")
        self.activity_detail.setText(text)
        self._log(f"错误：{text}")
        self._running(False)
        QMessageBox.critical(self, "转换失败", text)

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

    def _running(self, running):
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    def _log(self, text):
        self.log_edit.appendPlainText(
            f"[{time.strftime('%H:%M:%S')}] {text}"
        )

    def _save(self):
        self.settings.setValue("output", self.output_edit.text())
        for key, value in self._snapshot().items():
            if key != "regions":
                self.settings.setValue(key, value)

    def _restore(self):
        saved_output = self.settings.value("output", "")
        if saved_output:
            self.output_edit.setText(str(normalize_output_root(saved_output)))
        self.geometry = self.settings.value("geometry", "print")
        for combo, key, default in (
            (self.format_combo, "format", "stl"),
            (self.water_combo, "water", "cube"),
            (self.fallback_combo, "fallback", "cube"),
            (self.emission_combo, "blender_lights", "exact"),
            (self.components_combo, "components", "keep"),
            (self.cavities_combo, "cavities", "preserve"),
            (self.boolean_combo, "boolean_fallback", "voxel32"),
        ):
            self._set_value(combo, self.settings.value(key, default))
        self.scale_spin.setValue(self.settings.value("scale", 1.0, float))
        self.thickness_spin.setValue(
            self.settings.value("thickness", 1 / 16, float)
        )
        self.emission_strength_spin.setValue(
            self.settings.value("emission_strength", 1.0, float)
        )
        self.component_spin.setValue(
            self.settings.value("min_component_volume", 1 / 4096, float)
        )
        self.center_check.setChecked(
            self.settings.value("center", False, bool)
        )
        self.color_check.setChecked(
            self.settings.value("color", False, bool)
        )
        self.textures_check.setChecked(
            self.settings.value("textures", True, bool)
        )
        self.emission_check.setChecked(
            self.settings.value("emission", True, bool)
        )
        self.emission_config_edit.setText(
            self.settings.value("emission_config", "")
        )

    def closeEvent(self, event: QCloseEvent):
        if self.worker_thread and self.worker_thread.isRunning():
            answer = QMessageBox.question(
                self, "转换仍在进行",
                "关闭窗口会终止当前转换，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_event.set()
            self.worker_thread.quit()
            self.worker_thread.wait(2500)
        self._save()
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
