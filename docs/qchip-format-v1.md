# `.qchip` 文件格式 v1

> 历史格式说明。当前写入格式为 [v2](qchip-format-v2.md)；打开 v1 文件时程序会先备份并迁移。

`.qchip` 是单芯片、单文件 SQLite 数据库。应用启动时启用外键检查；`PRAGMA user_version=1` 和 `meta.format=qchip` 用于识别格式。未来升级旧文件时必须先备份再迁移，遇到未知版本拒绝写入。应用使用 SQLite 默认回滚日志模式，以保持关闭后的文件为单文件。

## 实体与关系

| 表 | 内容 |
| --- | --- |
| `meta` | 芯片名称、模板、创建时间和模板尺寸；键值均为文本。 |
| `devices` | UUID、`qubit/coupler`、子类型、唯一名称、角色、逻辑坐标、显示偏移、备注。 |
| `couplings` | UUID、两个比特 UUID、`direct/coupler`、可选耦合器 UUID、备注。一个比特对可同时存在不同耦合方式。 |
| `wiring_lines` / `wiring_assignments` | 制冷机线路、端口、通道及线路到多个器件的映射。 |
| `parameter_definitions` | 稳定参数键、显示名、适用对象、设计/测量/自定义分类、数值/文本类型、单位。 |
| `parameter_records` | 参数的不可覆盖历史记录、命名工作点、偏置、记录时间、来源、当前值标志。 |
| `calibration_records` / `attachments` | 实验类型、拟合结果 JSON、备注，以及内嵌图片或其他附件的 MIME、SHA-256 和内容。 |

`devices.id`、`couplings.id` 等均为 UUID 字符串，不因移动或重命名而变化。删除器件时外键删除相关连接、参数与接线关联。SQLite `NULL` 表示未填写，与数值 `0` 不同。参数记录还可以用当前记录的空值表示主动清空。每个“对象 + 参数键 + 工作点”最多一条当前记录，旧记录保持在历史中。时间采用含 UTC 时区的 ISO 8601 文本。

频率默认 GHz、非谐与耦合强度 MHz、寿命 µs、读取功率 dBm、电容 fF、电流 nA；`EJ/h` 与 `EC/h` 以 GHz 登记。新字段通过 `parameter_definitions` 增加，不改动既有表结构。UI 可以以后按实验室现有表格调整预置字段，已存值仍按稳定键读取。

数据表与图片使用相同的当前值快照：选定工作点后查询 `is_current=1` 的记录。没有当前记录或值为 `NULL` 时，表格单元格为空，参数图显示灰色“未填写”。可选历史明细单独导出，不改变主表含义。

## 扩展约定

v1 只定义标定结果和内嵌附件接口，完整标定管理界面留待后续版本。`fit_json` 必须是合法 JSON，图片附件作为 BLOB 保存在同一个 `.qchip` 文件内。后续版本更新 `user_version`，通过独立迁移函数在备份后升级；未知版本不得静默重写。
