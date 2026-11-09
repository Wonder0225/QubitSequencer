"""Layout and command surfaces for the compact scientific workbench."""

from pathlib import Path
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QTableWidget, QToolButton, QVBoxLayout, QWidget,
)
from .editors import DeviceEditor
from .design_ui import ChipDesignPanel
from .energy_levels import EnergyLevelsPanel
from .wiring_store import LINE_TYPES


class ProjectTitle(QLabel):
    """Keep the project name readable without forcing the window wider."""

    def setText(self, text):
        self.full_text = str(text)
        self._elide()

    def _elide(self):
        if hasattr(self, "full_text"):
            super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight,
                                                        max(40, self.contentsRect().width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class WorkbenchLayout:
    def _action(self, text, callback, symbol, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        self._icon_bindings.append((action, symbol))
        return action

    def _tool_button(self, text, callback=None, symbol=None, role="secondary"):
        button = QToolButton()
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setProperty("role", role)
        if callback:
            button.clicked.connect(callback)
        if symbol:
            self._icon_bindings.append((button, symbol))
        return button

    def _build_ui(self):
        from .gui import CanvasToolbar, ChipView, TexComboBox, TexHeaderView, TexTableDelegate
        self._icon_bindings = []
        file_menu = self.menuBar().addMenu("文件")
        self.file_actions = {}
        definitions = [("new", "新建芯片", self.new_chip, "new", "Ctrl+N"),
                       ("open", "打开 .qchip", self.open_chip, "open", "Ctrl+O"),
                       ("backup", "另存备份", self.backup, "backup", "Ctrl+Shift+S"),
                       ("xlsx", "导出 XLSX", self.save_xlsx, "table", None),
                       ("csv", "导出 CSV", self.save_csv, "table", None),
                       ("image", "导出当前图片", self.save_image, "chip", None),
                       ("images", "批量导出参数图", self.save_all_images, "export", None)]
        self.export_menu = QMenu(self)
        self.recent_menu = QMenu("打开最近的文件", self)
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._refresh_recent_menu)
        self.recent_action = self._action("显示全部最近文件…", self.open_recent, "history", "Ctrl+R")
        self.addAction(self.recent_action)
        for key, text, callback, symbol, shortcut in definitions:
            action = self._action(text, callback, symbol, shortcut)
            self.file_actions[key] = action
            file_menu.addAction(action)
            if key == "open":
                file_menu.addMenu(self.recent_menu)
                file_menu.addSeparator()
            if key in ("xlsx", "csv", "image", "images"):
                self.export_menu.addAction(action)
        edit_menu = self.menuBar().addMenu("编辑")
        self.undo_action = self._action("撤回", self.undo_edit, "undo", "Ctrl+Z")
        self.redo_action = self._action("恢复（重做）", self.redo_edit, "redo", "Ctrl+Y")
        self.redo_action.setShortcuts(["Ctrl+Y", "Ctrl+Shift+Z"])
        edit_menu.addAction(self.undo_action); edit_menu.addAction(self.redo_action)
        self._update_undo_action()
        view_menu = self.menuBar().addMenu("视图")
        self.readout_visible_action = QAction("显示读取线和 filter", self)
        self.readout_visible_action.setCheckable(True); self.readout_visible_action.setChecked(True)
        self.readout_visible_action.toggled.connect(self._set_readout_visible)
        view_menu.addAction(self.readout_visible_action)
        self.dark_action = QAction("深色模式", self)
        self.dark_action.setCheckable(True)
        self.dark_action.toggled.connect(lambda checked: self.set_theme("dark" if checked else "light"))
        view_menu.addAction(self.dark_action)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self._action("使用说明 PDF", self.open_manual, "help"))
        self.version_action = self._action("版本信息", self.show_version_info, "help")
        help_menu.addAction(self.version_action)
        root = QWidget(); root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0); root_layout.setSpacing(0)
        self.setCentralWidget(root)
        project = QWidget(); project.setObjectName("projectBar")
        project_layout = QHBoxLayout(project)
        project_layout.setContentsMargins(16, 8, 16, 8); project_layout.setSpacing(8)
        brand = QLabel()
        brand.setPixmap(QIcon(str(Path(__file__).resolve().parent / "assets/app-icon.png")).pixmap(28, 28))
        project_layout.addWidget(brand)
        self.project_name = ProjectTitle(); self.project_name.setText("Qubit Sequencer")
        self.project_name.setProperty("role", "heading")
        self.project_name.setMinimumWidth(80); self.project_name.setMaximumWidth(320)
        self.project_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        project_layout.addWidget(self.project_name, 1); project_layout.addStretch(1)
        self.draft_status = QLabel(); self.draft_status.setProperty("role", "muted")
        project_layout.addWidget(self.draft_status)
        for key, text in (("new", "新建"), ("open", "打开"), ("backup", "备份")):
            self.file_actions[key].setIconText(text)
            button = QToolButton(); button.setDefaultAction(self.file_actions[key])
            button.setText(text); button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setProperty("role", "ghost"); project_layout.addWidget(button)
        project_layout.addSpacing(8)
        project_layout.addWidget(QLabel("工作点"))
        self.point_combo = QComboBox(); self.point_combo.setEditable(True)
        self.point_combo.addItems(["default", "idle", "readout", "max"])
        self.point_combo.setFixedWidth(112); self.point_combo.setToolTip("当前参数值和导出的工作点")
        project_layout.addWidget(self.point_combo)
        self.export_button = self._tool_button("导出", symbol="export")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_button.setMenu(self.export_menu); project_layout.addWidget(self.export_button)
        self.theme_button = QToolButton(); self.theme_button.setDefaultAction(self.dark_action)
        self.theme_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.theme_button.setProperty("role", "ghost")
        self._icon_bindings.append((self.dark_action, "moon")); project_layout.addWidget(self.theme_button)
        root_layout.addWidget(project)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        self.navigation = QListWidget(); self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(160); self.navigation.setIconSize(QSize(20, 20))
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setSpacing(4)
        for caption, symbol in (("芯片构型", "chip"), ("参数数据表", "table"),
                                ("制冷机接线", "wiring"), ("参数历史", "history")):
            item = QListWidgetItem(caption); item.setSizeHint(QSize(144, 40))
            self.navigation.addItem(item); self._icon_bindings.append((item, symbol))
        body.addWidget(self.navigation)
        self.tabs = QStackedWidget(); body.addWidget(self.tabs, 1)
        root_layout.addLayout(body, 1)
        diagram_page = QWidget(); diagram_layout = QVBoxLayout(diagram_page)
        diagram_layout.setContentsMargins(12, 12, 12, 12); diagram_layout.setSpacing(8)
        switch_row = QHBoxLayout()
        self.design_button = self._tool_button(
            "查看设计图", lambda: self._show_chip_design(self.chip_views.currentIndex() != 1), "chip")
        self.design_button.setToolTip("在芯片逻辑构型和实际设计图之间切换")
        switch_row.addWidget(self.design_button)
        self.energy_button = self._tool_button("查看能级图", self.show_energy_levels, "energy")
        self.energy_button.setToolTip("查看当前工作点的比特跃迁频率、完整图例和标定状态")
        switch_row.addWidget(self.energy_button); switch_row.addStretch()
        self.readout_visibility_button = QToolButton()
        self.readout_visibility_button.setDefaultAction(self.readout_visible_action)
        self.readout_visibility_button.setToolTip("显示／隐藏读取线、filter 及读取关联；保留所有登记数据")
        switch_row.addWidget(self.readout_visibility_button)
        diagram_layout.addLayout(switch_row)
        self.chip_views = QStackedWidget(); diagram_layout.addWidget(self.chip_views, 1)
        topology_page = QWidget(); topology_layout = QVBoxLayout(topology_page)
        topology_layout.setContentsMargins(0, 0, 0, 0); topology_layout.setSpacing(8)
        modes = QWidget(); modes_layout = QHBoxLayout(modes)
        modes_layout.setContentsMargins(0, 0, 0, 0); modes_layout.setSpacing(4)
        self.view_action = QAction("查看", self); self.view_action.setCheckable(True); self.view_action.setChecked(True)
        self.place_action = QAction("比特", self); self.place_action.setCheckable(True)
        self.place_coupler_action = QAction("耦合器", self); self.place_coupler_action.setCheckable(True)
        self.place_readout_action = QAction("读取线", self); self.place_readout_action.setCheckable(True)
        self.place_filter_action = QAction("可调 filter", self); self.place_filter_action.setCheckable(True)
        self.connect_action = QAction("连接", self); self.connect_action.setCheckable(True)
        self.add_component_button = self._tool_button("添加元件", symbol="new", role="ghost")
        self.add_component_button.setToolTip("选择元件后，在构型图上放置")
        self.add_component_menu = QMenu(self.add_component_button)
        self.add_component_menu.setToolTipsVisible(True)
        self.add_component_button.setMenu(self.add_component_menu)
        self.add_component_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        for action, symbol, tip in ((self.view_action, "select", "点击查看；拖动空白平移，滚轮缩放"),
                                    (self.place_action, "qubit", "点击格点放置比特"),
                                    (self.place_coupler_action, "coupler", "点击格点放置耦合器"),
                                    (self.place_readout_action, "readout", "依次点击起点和终点，自动吸附格点／半格；Esc 取消"),
                                    (self.place_filter_action, "filter", "点击读取线、空格点或半格放置可调 filter"),
                                    (self.connect_action, "connect", "依次点击两个器件建立连接")):
            action.setToolTip(tip); self._icon_bindings.append((action, symbol))
            if action in (self.view_action, self.connect_action):
                button = QToolButton(); button.setDefaultAction(action)
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                button.setProperty("role", "ghost"); modes_layout.addWidget(button)
            else:
                self.add_component_menu.addAction(action)
            action.toggled.connect(lambda checked, chosen=action: self._sync_mode(chosen, checked))
        modes_layout.insertWidget(1, self.add_component_button)
        layer = QWidget(); layer_layout = QHBoxLayout(layer)
        layer_layout.setContentsMargins(0, 0, 0, 0); layer_layout.setSpacing(8)
        self.connection_mode = QComboBox(); self.connection_mode.addItem("直接电容", "direct")
        self.connection_mode.addItem("经耦合器", "coupler"); self.connection_mode.setFixedWidth(112)
        self.connection_mode.setToolTip("连接模式下，选择两比特之间的耦合方式")
        self.connection_mode.setEnabled(False); layer_layout.addWidget(self.connection_mode)
        self.field_combo = TexComboBox()
        self.field_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.field_combo.setMinimumContentsLength(8); self.field_combo.setMinimumWidth(120)
        self.field_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.field_combo.setToolTip("选择构型或参数图层"); layer_layout.addWidget(self.field_combo, 1)
        fit = self._tool_button("适配芯片", self.fit_diagram, "fit")
        self.canvas_toolbar = CanvasToolbar(modes, layer, fit); topology_layout.addWidget(self.canvas_toolbar)
        self.diagram_splitter = QSplitter(); self.diagram_splitter.setChildrenCollapsible(False)
        self.view = ChipView(); self.diagram_splitter.addWidget(self.view)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.inspector_container = QWidget(); self.inspector_container.setObjectName("inspector")
        self.inspector_container.setMinimumWidth(260); self.inspector_container.setMaximumWidth(440)
        detail_layout = QVBoxLayout(self.inspector_container)
        detail_layout.setContentsMargins(12, 12, 12, 12); detail_layout.setSpacing(8)
        detail_head = QHBoxLayout()
        title = QLabel("详情"); title.setProperty("role", "heading"); detail_head.addWidget(title, 1)
        more = self._tool_button("", symbol="more", role="ghost")
        more.setToolTip("当前对象操作"); self.detail_menu = QMenu(self)
        self.delete_action = self._action("删除所选对象", self.delete_selected, "delete")
        self.delete_action.setShortcut("Delete"); self.delete_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.view.addAction(self.delete_action); self.detail_menu.addAction(self.delete_action)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup); more.setMenu(self.detail_menu)
        detail_head.addWidget(more)
        hide = self._tool_button("", lambda: self.set_inspector_visible(False), "close", "ghost")
        hide.setToolTip("收起详情"); detail_head.addWidget(hide); detail_layout.addLayout(detail_head)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail = QWidget(); contents = QVBoxLayout(detail); contents.setContentsMargins(0, 0, 0, 0); contents.setSpacing(8)
        self.selected_title = QLabel("点击器件或连接查看详情")
        self.selected_title.setTextFormat(Qt.TextFormat.RichText); self.selected_title.setWordWrap(True)
        contents.addWidget(self.selected_title)
        self.detail_editor = DeviceEditor(self.drafts)
        self.label_edit = self.detail_editor.label; self.subtype_edit = self.detail_editor.subtype
        self.role_edit = self.detail_editor.role; self.x_edit = self.detail_editor.x
        self.y_edit = self.detail_editor.y; self.notes_edit = self.detail_editor.notes
        contents.addWidget(self.detail_editor)
        self.readout_info = QLabel(); self.readout_info.setWordWrap(True)
        self.readout_info.setTextFormat(Qt.TextFormat.PlainText); self.readout_info.setProperty("role", "muted")
        contents.addWidget(self.readout_info)
        edit_buttons = QHBoxLayout()
        self.save_button = QPushButton("保存更改"); self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self.save_device); edit_buttons.addWidget(self.save_button)
        self.reset_button = QPushButton("取消更改"); self.reset_button.clicked.connect(self.reset_selected)
        edit_buttons.addWidget(self.reset_button); contents.addLayout(edit_buttons)
        note = QLabel("参数 · 双击登记新值"); note.setProperty("role", "muted"); contents.addWidget(note)
        self.secondary_parameters = QCheckBox("显示次要参数")
        self.secondary_parameters.hide()
        self.secondary_parameters.toggled.connect(self._refresh_secondary_parameters)
        contents.addWidget(self.secondary_parameters)
        self.parameter_table = QTableWidget(0, 3); self.parameter_table.setMinimumHeight(180)
        parameter_delegate = TexTableDelegate(self.parameter_table); parameter_delegate.label_columns = {0}
        self.parameter_table.setItemDelegate(parameter_delegate)
        self.parameter_table.cellDoubleClicked.connect(self.edit_parameter); contents.addWidget(self.parameter_table, 1)
        self.custom_button = QPushButton("添加自定义参数"); self.custom_button.setProperty("role", "ghost")
        self.custom_button.clicked.connect(self.add_definition); contents.addWidget(self.custom_button)
        scroll.setWidget(detail); detail.setBackgroundRole(QPalette.ColorRole.Base)
        scroll.viewport().setBackgroundRole(QPalette.ColorRole.Base)
        detail_layout.addWidget(scroll, 1)
        self.diagram_splitter.addWidget(self.inspector_container)
        self.diagram_splitter.setStretchFactor(0, 1); self.diagram_splitter.setStretchFactor(1, 0)
        width = max(260, min(440, int(self.settings.value("layout/inspectorWidth", 320))))
        self.diagram_splitter.setSizes([900, width]); self.diagram_splitter.splitterMoved.connect(self._remember_split)
        topology_layout.addWidget(self.diagram_splitter, 1)
        bottom = QHBoxLayout()
        self.mode_hint = QLabel("查看模式 · 拖动平移，滚轮缩放"); self.mode_hint.setProperty("role", "muted")
        bottom.addWidget(self.mode_hint, 1)
        self.inspector_toggle = self._tool_button("详情", lambda: self.set_inspector_visible(self.inspector_container.isHidden()),
                                                 "chevron", "ghost")
        self.inspector_toggle.setCheckable(True); bottom.addWidget(self.inspector_toggle)
        topology_layout.addLayout(bottom); self.chip_views.addWidget(topology_page)
        self.design_panel = ChipDesignPanel(device_combo=TexComboBox())
        self.design_panel.layout().setContentsMargins(0, 0, 0, 0)
        self.design_panel.saved.connect(self._written)
        self.design_panel.failed.connect(lambda message: self._error("设计图操作失败", message))
        self.design_panel.device_clicked.connect(self.on_design_device_clicked)
        self.chip_views.addWidget(self.design_panel)
        self.energy_panel = EnergyLevelsPanel()
        self.energy_panel.frequency_source.currentIndexChanged.connect(lambda: self._refresh_energy_levels())
        self.chip_views.addWidget(self.energy_panel)
        self.tabs.addWidget(diagram_page)
        data_page = QWidget(); data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(16, 12, 16, 12); data_layout.setSpacing(12)
        heading = QLabel("参数数据表"); heading.setProperty("role", "heading"); data_layout.addWidget(heading)
        row = QHBoxLayout(); row.setSpacing(8)
        self.table_kind = QComboBox(); self.table_kind.addItems(["比特", "耦合器", "读取线", "可调 filter", "读取线连接", "比特-耦合器连接", "耦合", "接线"])
        self.table_kind.currentTextChanged.connect(self.refresh_tables); row.addWidget(self.table_kind)
        self.table_orientation = QComboBox()
        self.table_orientation.addItem("横向 · 器件为行", "horizontal"); self.table_orientation.addItem("纵向 · 器件为列", "vertical")
        self.table_orientation.setCurrentIndex(self.table_orientation.findData("vertical"))
        self.table_orientation.currentIndexChanged.connect(self.refresh_tables); row.addWidget(self.table_orientation)
        self.parameter_visibility_button = self._tool_button("显示参数")
        self.parameter_visibility_menu = QMenu(self)
        self.parameter_visibility_button.setMenu(self.parameter_visibility_menu)
        self.parameter_visibility_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.parameter_visibility_button.setEnabled(False)
        row.addWidget(self.parameter_visibility_button)
        self.filter_edit = QLineEdit(); self.filter_edit.setPlaceholderText("筛选名称、类型、端口或参数")
        self.filter_edit.setClearButtonEnabled(True); self.filter_edit.textChanged.connect(self.filter_table); row.addWidget(self.filter_edit, 1)
        data_export = self._tool_button("导出", symbol="export")
        data_export.setMenu(self.export_menu); data_export.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(data_export); data_layout.addLayout(row)
        self.data_table = QTableWidget(); self.data_table.setHorizontalHeader(TexHeaderView(self.data_table))
        self.tex_delegate = TexTableDelegate(self.data_table); self.data_table.setItemDelegate(self.tex_delegate)
        self.data_table.cellDoubleClicked.connect(self.on_data_row_double_clicked); data_layout.addWidget(self.data_table, 1)
        self.table_summary = QLabel(); self.table_summary.setProperty("role", "muted"); data_layout.addWidget(self.table_summary)
        self.tabs.addWidget(data_page)
        wiring_page = QWidget(); wiring_layout = QVBoxLayout(wiring_page)
        wiring_layout.setContentsMargins(16, 12, 16, 12); wiring_layout.setSpacing(12)
        heading = QLabel("制冷机接线"); heading.setProperty("role", "heading"); wiring_layout.addWidget(heading)
        buttons = QHBoxLayout(); buttons.setSpacing(8); self.wiring_buttons = {}
        for text, callback in (("新增线路", self.add_line), ("编辑线路", self.edit_line),
                               ("关联器件", self.assign_line), ("解除关联", self.unassign_line), ("删除线路", self.delete_line)):
            button = QPushButton(text); button.clicked.connect(callback)
            button.setProperty("role", "primary" if text == "新增线路" else "danger" if text == "删除线路" else "secondary")
            buttons.addWidget(button); self.wiring_buttons[text] = button
        buttons.addStretch(); wiring_layout.addLayout(buttons)
        filters = QHBoxLayout()
        self.wiring_board = QComboBox(); self.wiring_board.addItem("全部盘号")
        self.wiring_usage = QComboBox(); self.wiring_usage.addItems(["全部用途"] + LINE_TYPES)
        self.wiring_search = QLineEdit(); self.wiring_search.setPlaceholderText("搜索端口、样品引脚、图纸编号、器件或元件")
        self.wiring_search.setMinimumWidth(100)
        filters.addWidget(self.wiring_board); filters.addWidget(self.wiring_usage); filters.addWidget(self.wiring_search, 1)
        self.wiring_board.currentTextChanged.connect(self.filter_wiring)
        self.wiring_usage.currentTextChanged.connect(self.filter_wiring)
        self.wiring_search.textChanged.connect(self.filter_wiring)
        wiring_layout.addLayout(filters)
        self.wiring_table = QTableWidget()
        wiring_delegate = TexTableDelegate(self.wiring_table); wiring_delegate.label_columns = {5}
        self.wiring_table.setItemDelegate(wiring_delegate)
        self.wiring_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.wiring_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.wiring_table.itemSelectionChanged.connect(self._update_ui_state)
        self.wiring_table.itemSelectionChanged.connect(self.refresh_wiring_detail)
        self.wiring_table.cellDoubleClicked.connect(lambda *_: self.edit_line())
        wiring_layout.addWidget(self.wiring_table, 1)
        self.wiring_detail = QLabel("选择线路查看温级配置、放大链与检线记录；双击线路可编辑。")
        self.wiring_detail.setWordWrap(True); self.wiring_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.wiring_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_scroll = QScrollArea(); detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(self.wiring_detail); detail_scroll.setMinimumHeight(140); detail_scroll.setMaximumHeight(200)
        wiring_layout.addWidget(detail_scroll)
        self.tabs.addWidget(wiring_page)
        history_page = QWidget(); history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(16, 12, 16, 12); history_layout.setSpacing(12)
        heading = QLabel("参数历史"); heading.setProperty("role", "heading"); history_layout.addWidget(heading)
        subtitle = QLabel("显示全部工作点的历史 · 北京时间 UTC+08:00 · 参数登记后立即写入文件")
        subtitle.setProperty("role", "muted"); history_layout.addWidget(subtitle)
        history_actions = QHBoxLayout()
        self.history_attachments_button = self._tool_button("管理附件", self.manage_history_attachments, "open")
        history_actions.addWidget(self.history_attachments_button)
        history_actions.addStretch()
        history_layout.addLayout(history_actions)
        self.history_table = QTableWidget()
        history_delegate = TexTableDelegate(self.history_table); history_delegate.label_columns = {1, 3}
        self.history_table.setItemDelegate(history_delegate)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setSortingEnabled(True)
        self.history_table.itemSelectionChanged.connect(self._update_ui_state)
        self.history_table.cellDoubleClicked.connect(self.manage_history_attachments)
        history_layout.addWidget(self.history_table, 1); self.tabs.addWidget(history_page)
        self.navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._page_changed); self.navigation.setCurrentRow(0)
        self.field_combo.currentIndexChanged.connect(self.refresh_scene)
        self.point_combo.currentTextChanged.connect(self.refresh_parameter_views)
        self.file_status = QLabel("未打开文件"); self.file_status.setProperty("role", "muted")
        self.statusBar().addPermanentWidget(self.file_status)
        self.set_inspector_visible(self.settings.value("layout/inspectorVisible", True, type=bool), persist=False)
        self._update_ui_state(); self.statusBar().showMessage("新建或打开 .qchip 文件开始")

    def _page_changed(self, index):
        self.navigation.blockSignals(True); self.navigation.setCurrentRow(index); self.navigation.blockSignals(False)
        self.first_endpoint = None
        self.view_action.setChecked(True)
        self.design_panel.edit_button.setChecked(False)
        if hasattr(self, "device_popup"):
            self.device_popup.close()
        self._update_ui_state()

    def _show_chip_design(self, visible):
        self._show_chip_view(1 if visible else 0)

    def _show_chip_view(self, index):
        self.first_endpoint = None
        self.view_action.setChecked(True)
        self.design_panel.edit_button.setChecked(False)
        if hasattr(self, "device_popup"):
            self.device_popup.close()
        self.chip_views.setCurrentIndex(index)
        self.design_button.setText("返回构型图" if index == 1 else "查看设计图")
        self.energy_button.setText("返回构型图" if index == 2 else "查看能级图")
        self._update_ui_state()

    def _remember_split(self, *_):
        sizes = self.diagram_splitter.sizes()
        if not self.inspector_container.isHidden() and len(sizes) > 1 and sizes[1]:
            self.settings.setValue("layout/inspectorWidth", sizes[1])

    def set_inspector_visible(self, visible, persist=True):
        self.inspector_container.setVisible(visible); self.inspector_toggle.setChecked(visible)
        if visible:
            width = max(260, min(440, int(self.settings.value("layout/inspectorWidth", 320))))
            self.diagram_splitter.setSizes([max(200, self.diagram_splitter.width() - width), width])
        if persist:
            self.settings.setValue("layout/inspectorVisible", visible)

    def _written(self, label):
        self.statusBar().showMessage(f"{label}已写入文件", 5000)
        self.file_status.setText(f"{self.store.path.name} · 已写入" if self.store else "未打开文件")

    def _update_ui_state(self, *_):
        if not hasattr(self, "wiring_buttons"):
            return
        opened = self.store is not None
        showing_topology = self.chip_views.currentIndex() == 0
        self.design_button.setEnabled(opened)
        self.energy_button.setEnabled(opened)
        self.add_component_button.setEnabled(opened and showing_topology)
        self.readout_visible_action.setEnabled(opened)
        for key, action in self.file_actions.items():
            action.setEnabled(opened or key in ("new", "open"))
        self.export_button.setEnabled(opened); self.point_combo.setEnabled(opened)
        if not opened:
            self.parameter_visibility_button.setEnabled(False)
        for action in (self.view_action, self.place_action, self.place_coupler_action,
                       self.place_readout_action, self.place_filter_action, self.connect_action):
            action.setEnabled(opened and showing_topology)
        self.connection_mode.setEnabled(opened and showing_topology and self.connect_action.isChecked())
        self.field_combo.setEnabled(opened and showing_topology)
        selected = bool(opened and self.selected)
        dirty = selected and self.drafts.dirty(*self.selected)
        self.save_button.setEnabled(bool(dirty)); self.reset_button.setEnabled(bool(dirty)); self.custom_button.setEnabled(selected)
        self.custom_button.setEnabled(selected and self.selected[0] != "readout_link")
        self.delete_action.setEnabled(selected and self.tabs.currentIndex() == 0 and showing_topology)
        count = len(self.drafts.dirty_keys()) if opened else 0
        self.draft_status.setText(f"{count} 项未保存" if count else "")
        line_selected = bool(opened and self._chosen_line_id())
        for text, button in self.wiring_buttons.items():
            button.setEnabled(opened if text == "新增线路" else line_selected)
        row = self.wiring_table.currentRow()
        association = row >= 0 and self.wiring_table.item(row, 5) and self.wiring_table.item(row, 5).text()
        self.wiring_buttons["解除关联"].setEnabled(bool(line_selected and association))
        self.history_attachments_button.setEnabled(bool(opened and self._chosen_history_id()))
