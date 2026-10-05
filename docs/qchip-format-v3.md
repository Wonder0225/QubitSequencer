# `.qchip` 文件格式 v3：参数标定附件

v3 延续单文件 SQLite 存储，新增每条参数历史的附件及版本管理。`PRAGMA user_version=3`，`meta.format=qchip`；参数、器件、连接、接线及旧独立标定表的定义沿用 [v2](qchip-format-v2.md)。

## 新增表

| 表 | 内容 |
| --- | --- |
| `record_attachments` | 附件 UUID、关联的参数记录 UUID、北京时间创建/更新时间、可恢复的移除标记。 |
| `attachment_versions` | 附件 UUID、递增版本号、文件名、MIME、可选文本编码、说明、北京时间及 SHA-256 内容引用。 |
| `attachment_blobs` | SHA-256 主键、字节数和原始文件 BLOB。多个附件/版本可复用相同内容。 |

附件关联 `parameter_records.id`，而非器件 ID、参数键或时间。一次登记可包含多个附件；比特、耦合器、物理连接和两比特耦合采用同一机制。

修改内容、文件名或说明时追加版本。恢复旧版本追加一个新版本，即使恢复的是当前版本；旧版本不会被改写。移除只改变标记，附件及所有版本保留。删除所属器件/连接导致参数记录级联删除时，对应附件版本也删除；不再被任何版本引用的内容由清理触发器移除。SQLite 文件空间不主动执行 VACUUM。

附件保存字节快照，不依赖外部路径。读取及导出前检查字节数与 SHA-256；`validate` 同时检查附件内容。UTF-8、UTF-8 BOM 和 GB18030 文本保存其编码，其他内容按原始字节保存。文件名不能包含路径分隔符、Windows 保留名称或不支持的字符。

## 接口

`AttachmentInput(file_name, data, mime_type="application/octet-stream", notes="", encoding=None)` 表示待保存文件，`data` 为 `bytes`。

```python
from qubit_sequencer.storage import ChipStore
from qubit_sequencer.attachment_store import AttachmentInput, AttachmentChange

with ChipStore.open("chip.qchip") as store:
    record_id = store.set_parameter(
        qubit_id, "qubit.f01", 5.1,
        attachments=[AttachmentInput("fit.py", b"frequency = 5.1\n",
                                     "text/x-python", encoding="utf-8")],
    )
    files = store.parameter_attachments(record_id)
    attachment_id = files[0]["id"]
    store.commit_attachment_changes(record_id, [
        AttachmentChange(
            attachment_id,
            AttachmentInput("fit.py", b"frequency = 5.1\n", "text/x-python",
                            notes="频率谱拟合", encoding="utf-8"),
            expected_version=files[0]["version"],
        ),
    ])
    store.restore_attachment(attachment_id, 1)
    store.export_attachment(attachment_id, "fit-copy.py")  # 目标必须不存在
```

- `record(record_id)` 返回原始参数历史。
- `parameter_attachments(record_id, include_removed=False)` 和 `attachment_versions(attachment_id)` 仅返回元数据，不加载 BLOB。
- `read_attachment(attachment_id, version=None)` 返回校验后的内容与版本信息；不指定版本时读取最新版本。
- `attachment_counts()` 返回各参数记录的有效附件数量。
- `commit_attachment_changes(record_id, changes)` 原子提交新增、修改与移除；`expected_version` 用于检测旧编辑状态。
- `restore_attachment(attachment_id, version)` 追加恢复版本并解除移除状态。
- `set_parameter(..., attachments=())` 保持返回参数记录 UUID；参数与全部附件在同一事务写入，失败回滚当前值标记。

GUI 的“保存副本”允许用户明确选择覆盖现有文件，采用原子写入；禁止覆盖当前打开的芯片文件。XLSX/CSV 保持原有工作表与列结构，不嵌入附件。

## 升级与兼容

打开 v2 文件前生成同目录 `文件.pre-v2.qchip` 备份，再用事务新增附件表并设置 v3。已有同名备份时停止升级，避免覆盖。v1 按 v1→v2→v3 顺序升级，备份保存最初打开的版本。迁移不改写参数历史或推断旧标定附件的归属。

旧的 `calibration_records`、`attachments` 和 `add_calibration` / `add_attachment` 接口保留。旧标定附件不会自动显示在某条参数历史中。数据库备份包含所有附件及版本。

**v3 文件可使用当前源码或包含附件管理的 Windows 0.3.1 发布版打开；旧 v2 程序无法打开 v3 文件。** 软件版本号保持 0.3.1。
