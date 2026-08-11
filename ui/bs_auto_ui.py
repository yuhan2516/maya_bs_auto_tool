# -*- coding: utf-8 -*-
from PySide2 import QtWidgets, QtCore, QtGui
import maya.cmds as cmds
import maya.OpenMayaUI as omui
from shiboken2 import wrapInstance

from ..core import scene_collector, abc_collector, matcher, bs_operator, name_parser


_TOOL_WINDOW = None


def get_maya_main_window():
    """
    获取 Maya 主窗口 QWidget，作为 UI 的 parent。
    """
    main_window_ptr = omui.MQtUtil.mainWindow()
    if main_window_ptr is None:
        return None
    try:
        return wrapInstance(int(main_window_ptr), QtWidgets.QWidget)
    except Exception:
        return wrapInstance(long(main_window_ptr), QtWidgets.QWidget)


class StatusColors(object):
    """结果列表状态颜色"""
    OK        = QtGui.QColor(150, 255, 150)
    CONFLICT  = QtGui.QColor(255, 200, 100)
    UNMATCHED = QtGui.QColor(255, 150, 150)
    NO_ABC    = QtGui.QColor(220, 220, 220)


class OriginalPickerDialog(QtWidgets.QDialog):
    """
    未匹配项手动选择原始模型的对话框。
    """
    def __init__(self, original_items, parent=None):
        super(OriginalPickerDialog, self).__init__(parent)

        self.setWindowTitle("Select Original Model")
        self.setMinimumSize(500, 400)

        self.original_items = original_items
        self.selected_item = None

        layout = QtWidgets.QVBoxLayout(self)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search by name or path...")
        self.search_edit.textChanged.connect(self._filter)
        layout.addWidget(self.search_edit)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_ok = QtWidgets.QPushButton("OK")
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        self._populate()

    def _populate(self):
        self.list_widget.clear()
        for item in self.original_items:
            display = "{}  |  {}".format(item["basename"], item["dag_path"])
            list_item = QtWidgets.QListWidgetItem(display)
            list_item.setData(QtCore.Qt.UserRole, item)
            self.list_widget.addItem(list_item)

    def _filter(self, text):
        text = text.lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text not in item.text().lower())

    def accept(self):
        current = self.list_widget.currentItem()
        if current:
            self.selected_item = current.data(QtCore.Qt.UserRole)
        super(OriginalPickerDialog, self).accept()

    def get_selected(self):
        return self.selected_item


class BlendShapeAutoUI(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super(BlendShapeAutoUI, self).__init__(parent)

        self.setWindowTitle("ABC BlendShape Auto Tool  v3.1 (Group Mode)")
        self.setMinimumSize(1100, 750)

        self.setWindowFlags(
            QtCore.Qt.Window |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.WindowCloseButtonHint |
            QtCore.Qt.WindowMinimizeButtonHint
        )

        self.abc_items = []
        self.original_items = []
        self.abc_root_long_paths = []
        self.match_result = {
            "matched": [],
            "conflicts": [],
            "unmatched": [],
            "unmatched_originals": []
        }

        self._build_ui()
        self._refresh_namespaces()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)

        # ----- 顶部按钮 -----
        btn_layout = QtWidgets.QHBoxLayout()

        self.btn_load_abc = QtWidgets.QPushButton("Load Selected ABC")
        self.btn_refresh_ns = QtWidgets.QPushButton("Refresh Namespaces")
        self.btn_auto_match = QtWidgets.QPushButton("Auto Match")
        self.btn_create_bs = QtWidgets.QPushButton("Create BlendShapes")
        self.btn_delete_bs = QtWidgets.QPushButton("Delete BlendShapes")

        self.btn_load_abc.clicked.connect(self._on_load_abc)
        self.btn_refresh_ns.clicked.connect(self._on_refresh_namespaces)
        self.btn_auto_match.clicked.connect(self._on_auto_match)
        self.btn_create_bs.clicked.connect(self._on_create_bs)
        self.btn_delete_bs.clicked.connect(self._on_delete_bs)

        btn_layout.addWidget(self.btn_load_abc)
        btn_layout.addWidget(self.btn_refresh_ns)
        btn_layout.addWidget(self.btn_auto_match)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_delete_bs)
        btn_layout.addWidget(self.btn_create_bs)

        main_layout.addLayout(btn_layout)

        # ----- Align Root -----
        align_layout = QtWidgets.QHBoxLayout()
        align_label = QtWidgets.QLabel("Align Root:")
        self.align_root_edit = QtWidgets.QLineEdit()
        self.align_root_edit.setPlaceholderText("Auto detect from ABC top group, e.g. Cloth_wrap")
        self.align_root_edit.setReadOnly(True)

        align_layout.addWidget(align_label)
        align_layout.addWidget(self.align_root_edit)
        main_layout.addLayout(align_layout)

        # ----- 中间区域 -----
        middle_layout = QtWidgets.QHBoxLayout()

        # Namespace 列表
        ns_group = QtWidgets.QGroupBox("Namespaces")
        ns_layout = QtWidgets.QVBoxLayout(ns_group)
        self.ns_list = QtWidgets.QListWidget()
        self.ns_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        ns_layout.addWidget(self.ns_list)
        middle_layout.addWidget(ns_group, 1)

        # ABC Meshes 列表
        abc_group = QtWidgets.QGroupBox("ABC Meshes")
        abc_layout = QtWidgets.QVBoxLayout(abc_group)
        self.abc_list = QtWidgets.QTableWidget()
        self.abc_list.setColumnCount(3)
        self.abc_list.setHorizontalHeaderLabels(["Basename", "Relative Path", "Long Name"])
        self.abc_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.abc_list.horizontalHeader().setStretchLastSection(True)
        abc_layout.addWidget(self.abc_list)
        middle_layout.addWidget(abc_group, 3)

        main_layout.addLayout(middle_layout, 2)

        # ----- Match Results -----
        result_group = QtWidgets.QGroupBox("Match Results (right-click for manual assign)")
        result_layout = QtWidgets.QVBoxLayout(result_group)
        self.result_list = QtWidgets.QTableWidget()
        self.result_list.setColumnCount(4)
        self.result_list.setHorizontalHeaderLabels(["Status", "Match Key", "Original Model", "ABC Target"])
        self.result_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.result_list.horizontalHeader().setStretchLastSection(True)
        self.result_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.result_list.customContextMenuRequested.connect(self._on_result_context_menu)
        result_layout.addWidget(self.result_list)
        main_layout.addWidget(result_group, 3)

        # ----- Options -----
        options_group = QtWidgets.QGroupBox("Options")
        options_layout = QtWidgets.QFormLayout(options_group)

        self.name_pattern_edit = QtWidgets.QLineEdit("{base}_{target}_bbs")
        self.weight_spin = QtWidgets.QDoubleSpinBox()
        self.weight_spin.setRange(0.0, 1.0)
        self.weight_spin.setSingleStep(0.1)
        self.weight_spin.setValue(1.0)

        self.deformation_order_combo = QtWidgets.QComboBox()
        self.deformation_order_combo.addItem("Before", "before")
        self.deformation_order_combo.addItem("After", "after")
        self.deformation_order_combo.addItem("Parallel", "parallel")
        self.deformation_order_combo.addItem("Split", "split")
        self.deformation_order_combo.addItem("Default", "default")
        self.deformation_order_combo.setToolTip(
            "Maya blendShape deformation order used when creating the node."
        )

        self.single_bs_chk = QtWidgets.QCheckBox("Use Single BlendShape Node via Group hierarchy")
        self.single_bs_chk.setChecked(True)
        self.single_bs_chk.setToolTip(
            "If matched perfectly, script will find the top common parent group and pass it to a single blendShape node."
        )

        self.hide_target_chk = QtWidgets.QCheckBox("Hide ABC target group after success")
        self.hide_target_chk.setChecked(True)

        options_layout.addRow("BS Name Pattern:", self.name_pattern_edit)
        options_layout.addRow("Weight:", self.weight_spin)
        options_layout.addRow("Deformation Order:", self.deformation_order_combo)
        options_layout.addRow(self.single_bs_chk)
        options_layout.addRow(self.hide_target_chk)

        main_layout.addWidget(options_group)

        # ----- Info Label -----
        self.info_label = QtWidgets.QLabel("Ready")
        main_layout.addWidget(self.info_label)

        # ----- Log -----
        self.log_edit = QtWidgets.QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(120)
        main_layout.addWidget(self.log_edit)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _log(self, message):
        self.log_edit.append(message)

    def _set_info(self, text):
        self.info_label.setText(text)

    def _on_load_abc(self):
        data = abc_collector.collect_abc_meshes_from_selection()
        self.abc_items = data.get("items", [])
        self.abc_root_long_paths = data.get("root_long_paths", [])

        align_root = data.get("align_root", "")
        self.align_root_edit.setText(align_root)

        self._auto_select_namespace(align_root, clear_first=True)
        self._populate_abc_list()

        self._log(u"加载了 {} 个 ABC mesh，Align Root: '{}'".format(
            len(self.abc_items), align_root))

        for item in self.abc_items:
            root = item.get("root_long_path", "")
            if name_parser.has_multiple_root_occurrences(item["dag_path"], root):
                self._log(u"[Warning] ABC 路径中 root '{}' 出现多次：{}".format(
                    root, item["dag_path"]))

    def _on_refresh_namespaces(self):
        self._refresh_namespaces()

    def _refresh_namespaces(self):
        self.ns_list.clear()
        namespaces = scene_collector.collect_user_namespaces()
        for ns in namespaces:
            item = QtWidgets.QListWidgetItem(ns)
            self.ns_list.addItem(item)

        self._auto_select_namespace(self.align_root_edit.text().strip(), clear_first=True)
        self._log(u"刷新 namespace：{} 个。".format(len(namespaces)))

    def _auto_select_namespace(self, ns_name, clear_first=False):
        if not ns_name:
            return
        if clear_first:
            self.ns_list.clearSelection()
        for i in range(self.ns_list.count()):
            item = self.ns_list.item(i)
            if item.text() == ns_name:
                item.setSelected(True)

    def _on_auto_match(self):
        selected_ns = [item.text() for item in self.ns_list.selectedItems()]
        if not selected_ns:
            QtWidgets.QMessageBox.warning(self, "Warning", u"请至少选择一个 namespace。")
            return

        if not self.abc_items:
            QtWidgets.QMessageBox.warning(self, "Warning", u"请先加载 ABC mesh。")
            return

        collection = scene_collector.collect_original_meshes(
            selected_ns,
            align_roots=self.abc_root_long_paths
        )

        self.original_items = collection.get("items", [])
        skipped = collection.get("skipped", [])

        if not self.original_items:
            QtWidgets.QMessageBox.warning(
                self, "Warning",
                u"在选中的 namespace 中未找到对应 root 的模型。"
            )
            return

        if skipped:
            self._log(u"[Info] 跳过了 {} 个不在 root 下或 namespace 不匹配的 mesh。".format(len(skipped)))

        previous_matched = list(self.match_result.get("matched", []))

        self.match_result = matcher.match_abc_to_original(
            self.abc_items, self.original_items)

        self._merge_previous_matches(previous_matched)
        self._populate_result_list()

        matched = len(self.match_result["matched"])
        conflicts = len(self.match_result["conflicts"])
        unmatched = len(self.match_result["unmatched"])
        unmatched_orig = len(self.match_result["unmatched_originals"])

        self._log(u"匹配完成：matched={}, conflicts={}, unmatched={}".format(
            matched, conflicts, unmatched))

        if unmatched_orig > 0:
            self._log(u"[Warning] 有 {} 个原始模型在 root 下但未被 ABC 匹配，将不会创建 BS。".format(
                unmatched_orig))

        if bs_operator.should_use_single_blendshape(
                self.match_result, self.abc_items, self.original_items):
            self._set_info(u"原始模型与 ABC 层级完全一致，将向上查找 Group 并使用单节点模式。")
        else:
            self._set_info(u"将为每个匹配 mesh 单独创建 blendShape 节点。")

        for item in self.abc_items:
            root = item.get("root_long_path", "")
            if name_parser.has_multiple_root_occurrences(item["dag_path"], root):
                self._log(u"[Warning] ABC 路径中 root '{}' 出现多次：{}".format(
                    root, item["dag_path"]))

    def _merge_previous_matches(self, previous_matched):
        if not previous_matched:
            return

        current_orig_paths = {o["dag_path"] for o in self.original_items}
        matched_abc_paths = {abc["dag_path"] for _, abc in self.match_result["matched"]}

        for orig, abc in previous_matched:
            if orig["dag_path"] not in current_orig_paths:
                continue

            abc_path = abc["dag_path"]
            if abc_path in matched_abc_paths:
                continue

            self.match_result["conflicts"] = [
                (a, c) for a, c in self.match_result["conflicts"]
                if a["dag_path"] != abc_path
            ]
            self.match_result["unmatched"] = [
                a for a in self.match_result["unmatched"]
                if a["dag_path"] != abc_path
            ]

            self.match_result["matched"].append((orig, abc))
            matched_abc_paths.add(abc_path)

        self._recompute_unmatched_originals()

    def _on_result_context_menu(self, position):
        row = self.result_list.rowAt(position.y())
        if row < 0:
            return

        status_item = self.result_list.item(row, 0)
        if not status_item:
            return

        status = status_item.text()
        if status not in ("UNMATCHED", "CONFLICT"):
            return

        abc_item = status_item.data(QtCore.Qt.UserRole)
        if not abc_item:
            return

        menu = QtWidgets.QMenu()
        action_assign = menu.addAction("Manually Assign Original Model")
        action = menu.exec_(self.result_list.viewport().mapToGlobal(position))

        if action == action_assign:
            self._manual_assign(row, abc_item)

    def _manual_assign(self, row, abc_item):
        if not self.original_items:
            selected_ns = [item.text() for item in self.ns_list.selectedItems()]
            collection = scene_collector.collect_original_meshes(
                selected_ns,
                align_roots=self.abc_root_long_paths
            )
            self.original_items = collection.get("items", [])

        if not self.original_items:
            QtWidgets.QMessageBox.warning(self, "Warning", u"没有可用的原始模型。")
            return

        dialog = OriginalPickerDialog(self.original_items, self)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        selected_orig = dialog.get_selected()
        if not selected_orig:
            return

        abc_path = abc_item["dag_path"]

        self.match_result["matched"] = [
            (o, a) for o, a in self.match_result["matched"]
            if a["dag_path"] != abc_path
        ]
        self.match_result["matched"].append((selected_orig, abc_item))

        self.match_result["conflicts"] = [
            (a, c) for a, c in self.match_result["conflicts"]
            if a["dag_path"] != abc_path
        ]
        self.match_result["unmatched"] = [
            a for a in self.match_result["unmatched"]
            if a["dag_path"] != abc_path
        ]

        self._recompute_unmatched_originals()
        self._populate_result_list()
        self._log(u"手动指定：{} -> {}".format(abc_path, selected_orig["dag_path"]))

    def _recompute_unmatched_originals(self):
        matched_orig_paths = {orig["dag_path"] for orig, _ in self.match_result["matched"]}
        self.match_result["unmatched_originals"] = [
            orig for orig in self.original_items
            if orig.get("relative_path") is not None
            and orig["dag_path"] not in matched_orig_paths
        ]

    def _on_create_bs(self):
        matched = self.match_result.get("matched", [])
        if not matched:
            QtWidgets.QMessageBox.warning(self, "Warning", u"没有可匹配的模型对。")
            return

        pattern = self.name_pattern_edit.text()
        weight = self.weight_spin.value()
        deformation_order = self.deformation_order_combo.currentData()
        use_single = self.single_bs_chk.isChecked()
        hide_target = self.hide_target_chk.isChecked()

        single_mode = False
        if use_single:
            single_mode = bs_operator.should_use_single_blendshape(
                self.match_result, self.abc_items, self.original_items)

        mode_text = u"单节点模式" if single_mode else u"多节点模式"

        cmds.undoInfo(openChunk=True)
        created_count = 0
        skip_count = 0
        fail_count = 0
        hidden_count = 0

        try:
            if single_mode:
                bs_node, messages, hidden = bs_operator.create_single_blendshape(
                    matched,
                    name_pattern=pattern,
                    weight=weight,
                    deformation_order=deformation_order,
                    skip_existing=True,
                    hide_target_after=hide_target
                )

                for msg in messages:
                    code, body = self._parse_bs_message(msg)
                    self._log(u"[{}] {}".format(code, body))
                    if code == "OK":
                        created_count += 1
                    elif code == "SKIP":
                        skip_count += 1
                    else:
                        fail_count += 1

                hidden_count = hidden if hidden else 0

            else:
                for orig, abc in matched:
                    bs_node, msg, hidden = bs_operator.create_blendshape(
                        base_dag=orig["dag_path"],
                        target_dag=abc["dag_path"],
                        name_pattern=pattern,
                        weight=weight,
                        deformation_order=deformation_order,
                        skip_existing=True,
                        hide_target_after=hide_target
                    )

                    code, body = self._parse_bs_message(msg)
                    self._log(u"[{}] {}".format(code, body))

                    if code == "OK":
                        created_count += 1
                        if hidden:
                            hidden_count += 1
                    elif code == "SKIP":
                        skip_count += 1
                    else:
                        fail_count += 1

        finally:
            cmds.undoInfo(closeChunk=True)

        order_text = self.deformation_order_combo.currentText()
        self._log(u"模式：{}，Deformation Order：{}，隐藏目标 {} 个。".format(
            mode_text, order_text, hidden_count))

        QtWidgets.QMessageBox.information(
            self,
            "Done",
            u"创建完成：成功 {}，跳过 {}，失败 {}。".format(created_count, skip_count, fail_count)
        )

        self._populate_result_list()

    def _get_selected_matched_pairs(self):
        matched = self.match_result.get("matched", [])
        selected_rows = {index.row() for index in self.result_list.selectedIndexes()}
        if not selected_rows:
            return matched

        selected_abc_paths = set()
        for row in selected_rows:
            status_item = self.result_list.item(row, 0)
            if not status_item or status_item.text() != "OK":
                continue
            abc_item = status_item.data(QtCore.Qt.UserRole)
            if abc_item:
                selected_abc_paths.add(abc_item.get("dag_path"))

        if not selected_abc_paths:
            return []

        return [
            (orig, abc) for orig, abc in matched
            if abc.get("dag_path") in selected_abc_paths
        ]

    def _on_delete_bs(self):
        matched = self._get_selected_matched_pairs()
        if not matched:
            QtWidgets.QMessageBox.warning(self, "Warning", u"没有可删除的匹配项。")
            return

        pattern = self.name_pattern_edit.text()
        use_single = self.single_bs_chk.isChecked()
        show_target = True
        all_matched = self.match_result.get("matched", [])

        single_mode = False
        if use_single:
            single_mode = bs_operator.should_use_single_blendshape(
                self.match_result, self.abc_items, self.original_items)
            if single_mode:
                matched = all_matched

        mode_text = u"单节点模式" if single_mode else u"多节点模式"
        deleted_count = 0
        skip_count = 0
        fail_count = 0
        shown_count = 0

        cmds.undoInfo(openChunk=True)
        try:
            if single_mode:
                messages, deleted_count, shown_count = bs_operator.delete_single_blendshape(
                    matched,
                    name_pattern=pattern,
                    show_target_after=show_target
                )
                for msg in messages:
                    code, body = self._parse_bs_message(msg)
                    self._log(u"[{}] {}".format(code, body))
                    if code == "SKIP":
                        skip_count += 1
                    elif code == "FAIL":
                        fail_count += 1
            else:
                for orig, abc in matched:
                    deleted, msg, shown = bs_operator.delete_blendshape(
                        base_dag=orig["dag_path"],
                        target_dag=abc["dag_path"],
                        name_pattern=pattern,
                        show_target_after=show_target
                    )
                    code, body = self._parse_bs_message(msg)
                    self._log(u"[{}] {}".format(code, body))

                    if deleted:
                        deleted_count += 1
                        if shown:
                            shown_count += 1
                    elif code == "SKIP":
                        skip_count += 1
                    else:
                        fail_count += 1
        finally:
            cmds.undoInfo(closeChunk=True)

        self._log(u"删除模式：{}，恢复显示目标 {} 个。".format(mode_text, shown_count))

        QtWidgets.QMessageBox.information(
            self,
            "Done",
            u"删除完成：成功 {}，跳过 {}，失败 {}。".format(deleted_count, skip_count, fail_count)
        )

    def _parse_bs_message(self, msg):
        msg = msg or ""
        if u"成功" in msg:
            return "OK", msg
        if u"跳过" in msg:
            return "SKIP", msg
        return "FAIL", msg

    # ------------------------------------------------------------------
    # 列表刷新
    # ------------------------------------------------------------------
    def _populate_abc_list(self):
        self.abc_list.setRowCount(0)
        self.abc_list.blockSignals(True)
        try:
            self.abc_list.setRowCount(len(self.abc_items))
            for row, item in enumerate(self.abc_items):
                rel_text = item.get("relative_path") or ""
                if not rel_text:
                    rel_text = "(direct under root)"

                basename_item = QtWidgets.QTableWidgetItem(item["basename"])
                rel_item = QtWidgets.QTableWidgetItem(rel_text)
                path_item = QtWidgets.QTableWidgetItem(item["dag_path"])

                for col_item in (basename_item, rel_item, path_item):
                    col_item.setData(QtCore.Qt.UserRole, item)
                    col_item.setFlags(col_item.flags() & ~QtCore.Qt.ItemIsEditable)

                self.abc_list.setItem(row, 0, basename_item)
                self.abc_list.setItem(row, 1, rel_item)
                self.abc_list.setItem(row, 2, path_item)
        finally:
            self.abc_list.blockSignals(False)

        self.abc_list.resizeColumnsToContents()

    def _populate_result_list(self):
        self.result_list.setRowCount(0)
        self.result_list.blockSignals(True)
        try:
            rows = (
                len(self.match_result["matched"]) +
                len(self.match_result["conflicts"]) +
                len(self.match_result["unmatched"]) +
                len(self.match_result["unmatched_originals"])
            )
            self.result_list.setRowCount(rows)

            row = 0

            for orig, abc in self.match_result["matched"]:
                key = self._get_match_key(abc)
                row = self._set_result_row(
                    row, status="OK", status_color=StatusColors.OK,
                    match_key=key, orig_text=orig["dag_path"], abc_item=abc, target_text=abc["dag_path"]
                )

            for abc, candidates in self.match_result["conflicts"]:
                key = self._get_match_key(abc)
                orig_text = u"多个候选：{}".format(", ".join([o["dag_path"] for o in candidates]))
                row = self._set_result_row(
                    row, status="CONFLICT", status_color=StatusColors.CONFLICT,
                    match_key=key, orig_text=orig_text, abc_item=abc, target_text=abc["dag_path"]
                )

            for abc in self.match_result["unmatched"]:
                key = self._get_match_key(abc)
                row = self._set_result_row(
                    row, status="UNMATCHED", status_color=StatusColors.UNMATCHED,
                    match_key=key, orig_text="-", abc_item=abc, target_text=abc["dag_path"]
                )

            for orig in self.match_result["unmatched_originals"]:
                key = self._get_match_key(orig)
                row = self._set_result_row(
                    row, status="NO_ABC", status_color=StatusColors.NO_ABC,
                    match_key=key, orig_text=orig["dag_path"], abc_item=None, target_text=u"(no abc)"
                )

        finally:
            self.result_list.blockSignals(False)

        self.result_list.resizeColumnsToContents()

    def _set_result_row(self, row, status, status_color, match_key,
                        orig_text, abc_item, target_text):
        status_item = QtWidgets.QTableWidgetItem(status)
        status_item.setBackground(status_color)
        status_item.setData(QtCore.Qt.UserRole, abc_item)
        status_item.setFlags(status_item.flags() & ~QtCore.Qt.ItemIsEditable)

        key_item = QtWidgets.QTableWidgetItem(match_key)
        key_item.setFlags(key_item.flags() & ~QtCore.Qt.ItemIsEditable)

        orig_item = QtWidgets.QTableWidgetItem(orig_text)
        orig_item.setFlags(orig_item.flags() & ~QtCore.Qt.ItemIsEditable)

        target_item = QtWidgets.QTableWidgetItem(target_text)
        target_item.setFlags(target_item.flags() & ~QtCore.Qt.ItemIsEditable)

        self.result_list.setItem(row, 0, status_item)
        self.result_list.setItem(row, 1, key_item)
        self.result_list.setItem(row, 2, orig_item)
        self.result_list.setItem(row, 3, target_item)

        return row + 1

    def _get_match_key(self, item):
        if hasattr(matcher, "get_match_key"):
            return matcher.get_match_key(item) or ""
        return matcher._get_match_key(item) or ""


def show():
    global _TOOL_WINDOW

    maya_main = get_maya_main_window()

    if _TOOL_WINDOW is not None:
        try:
            _TOOL_WINDOW.close()
            _TOOL_WINDOW.deleteLater()
        except Exception:
            pass
        _TOOL_WINDOW = None

    _TOOL_WINDOW = BlendShapeAutoUI(parent=maya_main)
    _TOOL_WINDOW.show()

    _TOOL_WINDOW.raise_()
    _TOOL_WINDOW.activateWindow()

    return _TOOL_WINDOW
