# `.qchip` 文件格式 v6：可选读取线与可调 filter

v6 保留 [v5](qchip-format-v5.md) 的表和字段，扩展器件及参数类型，并登记共享读取关联：

- `devices.kind` 允许 `qubit`、`coupler`、`readout`、`filter`。新类型的 `subtype` 分别为 `readout_line`、`tunable_filter`。
- `parameter_definitions.scope` 同样增加 `readout` 和 `filter`。默认新增 `readout.resonator_frequency`（读取腔频，GHz）和 `filter.bias_z`（Z 偏置，原始控制值，不预设物理单位）。参数记录仍通过 `device_id` 关联，也可添加自定义字段。
- `devices` 增加 `readout_length`（正数，默认 2 格）及 `readout_direction`（`horizontal` / `vertical`，默认横向）。其他器件不使用这两个字段。

```sql
CREATE TABLE readout_links (
    id TEXT PRIMARY KEY,
    readout_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    position REAL CHECK(position>=0 AND position<=1),
    notes TEXT NOT NULL DEFAULT '', UNIQUE(readout_id,device_id)
);
CREATE UNIQUE INDEX filter_readout ON readout_links(device_id)
    WHERE position IS NOT NULL;
```

读取线可关联多个比特，比特关联的 `position` 为空。filter 保存在线上的相对位置 `position`（0–1），只能属于一条读取线，可重新关联。其逻辑坐标随读取线几何与附近器件的避让位置更新；一次修改形成一个撤回操作。filter 位置不足时放置整体失败，不留下部分元件。

读取线保持直线，比特连接其最近点；显示读取线时淡化耦合器及比特—耦合器连接。显示开关不改变存储数据；设计图标注位置仍由用户手动登记，与逻辑线段坐标独立。

读取线和 filter 由用户手动添加，现有模板和项目不自动增加元件。名称、构型位置、备注、线路关联及参数均复用器件机制。设计图标注继续使用 `design_markers.device_id`，位置保存为原图归一化坐标，旋转不改变存储位置。

v5→v6 在事务内重建 `devices` 与 `parameter_definitions` 的 CHECK 约束，保留原字段，新增几何默认值、读取关联表及两项参数定义，检查外键后提交。升级前自动生成 `.pre-v5.qchip`（更旧格式采用其原版本号）备份。旧设计图、标注、接线、附件与参数历史保持原关联。

XLSX/CSV 增加“读取线”“可调 filter”“读取线连接”表；CSV 文件名为 `readout_lines.csv`、`filters.csv`、`readout_links.csv`，保留几何、关联、相对位置及参数信息。没有添加这些元件时对应表为空。

本次只更新源码，软件版本号仍为 0.3.2。已发布 Windows 0.3.2 使用 v4 格式；升级后的项目需使用当前源码，切回发布版时使用升级前备份。
