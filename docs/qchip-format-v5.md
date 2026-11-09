# `.qchip` 文件格式 v5：制冷机接线登记

v5 延续 [v4](qchip-format-v4.md) 的全部表，仅新增以下表。软件版本仍为 0.3.2，尚未编译此源码改动。

```sql
CREATE TABLE wiring_details (
    line_id TEXT PRIMARY KEY REFERENCES wiring_lines(id) ON DELETE CASCADE,
    details_json TEXT NOT NULL
);
```

每条线路最多一份结构化登记，保存为 UTF-8 JSON。`board`、`instrument`、`cable`、`sample_pin`、`drawing_label` 保存盘位、仪器、线缆及样品映射文字。

| 数组 | 每行字段 |
| --- | --- |
| `components` | `stage`, `kind`, `name`, `value`, `unit`, `terminal`, `notes`；数组顺序为人工登记的信号顺序 |
| `amplifiers` | `stage`, `model`, `serial`, `band`, `pins`, `notes` |
| `biases` | `amplifier`, `temperature` [K], `vd` [V], `id` [mA], `vg` [V], `notes` |
| `checks` | `time`, `sample_state`, `power` [dBm], `band` [GHz], `transmission` [dB], `result`, `notes` |

元件数值、温度、电压、电流、检线功率及传输保存为有限数值或 `null`。未登记项为 `null`，零值保留。元件单位独立保存；放大器和测试频段为范围文字。放大器工作参数通过人工编号对应，不增加独立放大器实体或推断关联。

`line_details(line_id)` 返回登记，没有登记时返回 `{}`。原 `add_line`、`update_line` 增加可选关键字 `details`，原有位置参数调用保持兼容；省略 `details` 的修改保留已有登记。基本信息、器件关联及新登记在同一事务中保存，失败整体回滚。线路删除级联删除登记。

v4→v5 在事务内新增一张表，不改写原记录。现有迁移机制在升级前自动备份，原有字段和新增类别不做自动推断。撤回／恢复沿用当前会话历史；备份与导出包含已保存的登记。
