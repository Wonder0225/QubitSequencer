"""Preview matching and atomic application of selected wiring endpoints."""
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel, QMessageBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .fridge_store import stored_line_type


def natural(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", str(value)) if part)


def linked_sample(chip, fridge):
    return next((s for s in fridge.samples() if fridge.sample_path(s['id']) == Path(chip.path).resolve()), None)


def compatible(box, port):
    expected = {'read_in': '读取输入', 'read_out': '读取输出'}.get(box['role'])
    return expected is None or stored_line_type(port['kind']) == expected


def diagram_values(chip, panel):
    if not panel or panel.preview:
        return {}, '未打开制冷机项目；仅显示旧接线'
    fridge = panel.store
    sample = linked_sample(chip, fridge)
    if not sample:
        return {}, '当前样品未关联制冷机；仅显示旧接线'
    if not fridge.sample_path(sample['id']).is_file():
        return {}, '关联样品文件不可用；仅显示旧接线'
    result = {key: {} for key in ('port', 'cryostat', 'line_type', 'channel')}
    cache = {}
    for p in fridge.ports():
        rows = list(fridge.branch_assignments(p['id'])) if fridge.circuit(p['id']).get('switch') else [p]
        for row in rows:
            if row['sample_id'] != sample['id']: continue
            _, _, devices = fridge.resolve_assignment(row, cache)
            branch = row['branch'] if 'branch' in row.keys() else None
            label = f"{p['board']}{p['number']}" + (f" / 分支 {branch}" if branch else '')
            for d in devices:
                for key, value in (('port', label), ('cryostat', fridge.name()), ('line_type', row['usage']), ('channel', '')):
                    if value:
                        result[key].setdefault(d['id'], []).append(f'制冷机: {value}')
    return {key: {ident: ', '.join(dict.fromkeys(values)) for ident, values in rows.items()}
            for key, rows in result.items()}, '制冷机项目与旧接线分别标注'


class QuickWiringDialog(QDialog):
    def __init__(self, host, chip, fridge, sample, devices, targets):
        super().__init__(host); self.setWindowTitle('快捷接线预览'); self.resize(920, 560)
        self.fridge, self.sample, self.chip = fridge, sample, chip
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('按两侧选择顺序逐一配对；读取线按读入、读出展开。类型不符或不可用时请调整预览。'))
        all_boxes = chip.sample_box_ports(); self.rows = []
        for device in (chip.device(ident) for ident in devices):
            boxes = [dict(p) for p in all_boxes if p['device_id'] == device['id']]
            boxes.sort(key=lambda p: (0 if p['role'] == 'read_in' else 1 if p['role'] == 'read_out' else 2, natural(p['number'])))
            if not boxes:
                self.rows.append((device['label'], None, '缺少样品盒接口'))
            else:
                self.rows.extend((device['label'], box, '') for box in boxes)
                if device['kind'] == 'readout':
                    for role, label in (('read_in', '读入'), ('read_out', '读出')):
                        if not any(p['role'] == role for p in boxes): self.rows.append((device['label'], None, f'缺少{label}接口'))
        used_box = {r['box_port_id'] for r in fridge.box_connections(sample['id'])}
        self.targets = [fridge.port(p) for p in targets]
        self.table = QTableWidget(len(self.rows), 5); self.table.setHorizontalHeaderLabels(['执行', '元件', '样品盒接口', '目标端口', '状态'])
        self.table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.table)
        next_target = 0
        self.errors = []
        for i, (label, box, error) in enumerate(self.rows):
            check = QTableWidgetItem(); check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setFlags(check.flags() & ~Qt.ItemFlag.ItemIsEditable)
            check.setCheckState(Qt.CheckState.Unchecked); self.table.setItem(i, 0, check)
            self.table.setItem(i, 1, QTableWidgetItem(label))
            self.table.setItem(i, 2, QTableWidgetItem((box['number'] + {'read_in':' · 读入', 'read_out':' · 读出'}.get(box['role'], '')) if box else '—'))
            combo = QComboBox(); combo.addItem('不分配', '')
            for p in self.targets:
                reason = ' · 开关需手动接线' if fridge.circuit(p['id']).get('switch') else ' · 已占用或损坏' if p['sample_id'] or p['manually_occupied'] else ''
                if not reason and box and not compatible(box, p): reason = ' · 接口类型不匹配'
                combo.addItem(f"{p['board']}{p['number']} · {p['kind']}{reason}", p['id'])
                if reason: combo.model().item(combo.count() - 1).setEnabled(False)
            if box and box['id'] in used_box: error = '样品盒接口已有连接'
            self.errors.append(error)
            if not box or (box and box['id'] in used_box): check.setFlags(check.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            consumes = box is not None or error == '缺少样品盒接口'
            match = self.targets[next_target] if consumes and next_target < len(self.targets) else None
            if consumes: next_target += 1
            if match:
                combo.setCurrentIndex(combo.findData(match['id']))
                valid = not error and box and not match['sample_id'] and not match['manually_occupied'] and not fridge.circuit(match['id']).get('switch') and compatible(box, match)
                if valid: check.setCheckState(Qt.CheckState.Checked)
            elif not error: error = '没有匹配的空闲端口'
            self.table.setCellWidget(i, 3, combo); self.table.setItem(i, 4, QTableWidgetItem(error or '可接线'))
            for column in (1, 2, 4):
                item = self.table.item(i, column); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            combo.currentIndexChanged.connect(self.update_preview)
        self.table.itemChanged.connect(self.update_preview)
        self.update_preview()
        self.table.resizeColumnsToContents()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText('确认接线')
        box.accepted.connect(self.accept); box.rejected.connect(self.reject); layout.addWidget(box)

    def update_preview(self, *_):
        selected = [self.table.cellWidget(i, 3).currentData() for i in range(len(self.rows))
                    if self.table.item(i, 0).checkState() == Qt.CheckState.Checked]
        self.table.blockSignals(True)
        for i in range(len(self.rows)):
            target = self.table.cellWidget(i, 3).currentData()
            port = next((p for p in self.targets if p['id'] == target), None)
            box = self.rows[i][1]
            target_error = ('开关端口需手动接线' if port and self.fridge.circuit(port['id']).get('switch') else
                            '端口已占用或损坏' if port and (port['sample_id'] or port['manually_occupied']) else
                            '接口类型不匹配' if port and box and not compatible(box, port) else '')
            reason = self.errors[i] or target_error or ('请选择目标端口' if not target else
                     '目标端口重复' if self.table.item(i, 0).checkState() == Qt.CheckState.Checked and selected.count(target) > 1 else '可接线')
            self.table.item(i, 4).setText(reason)
        self.table.blockSignals(False)

    def accept(self):
        selected = []
        for i, (_, box, _) in enumerate(self.rows):
            if self.table.item(i, 0).checkState() != Qt.CheckState.Checked: continue
            target = self.table.cellWidget(i, 3).currentData()
            if not box or not target:
                QMessageBox.warning(self, '无法接线', '勾选项必须指定已有样品盒接口和目标端口。'); return
            if self.table.item(i, 4).text() != '可接线':
                QMessageBox.warning(self, '无法接线', self.table.item(i, 4).text()); return
            port = next(p for p in self.targets if p['id'] == target)
            selected.append((box, target, (port['board'], port['number'], port['kind'])))
        if not selected:
            QMessageBox.information(self, '快捷接线', '请勾选需要接线的项目。'); return
        try:
            self.fridge.quick_assign(self.sample['id'], str(self.chip.path), selected)
        except Exception as exc:
            QMessageBox.warning(self, '快捷接线失败', f'{exc}\n请重新预览。'); return
        super().accept()
