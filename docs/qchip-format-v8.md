# `.qchip` 文件格式 v8：比特图纸编号

v8 保留 [v7 结构](qchip-format-v7.md)，在 `devices` 表新增 `drawing_number TEXT NOT NULL DEFAULT ''`。界面仅向量子比特提供此固定文本字段；编号与工作点无关，在比特详情、数据表与导出中显示。

打开 v1–v7 项目前，在同目录创建 `.pre-vN.qchip` 备份；v7 到 v8 的升级在事务中添加空字段，不改变器件 ID、已有参数、接线或历史图纸文字。旧软件可能无法打开升级后的项目，回退时使用升级前备份。

`.qfridge` 按样品文件和器件 ID 动态读取比特编号，不把此字段与已有线路上的历史图纸文字合并。详见 [制冷机格式 v2](qfridge-format-v2.md)。
