# 样品盒与制冷机接线文件格式

## `.qchip` v10

应用样品盒模板后，`meta` 的 `sample_box.template` 保存模板标识，`sample_box.appearance` 保存模板名称和图形参数的快照。`sample_box_ports` 保存接口 ID、编号、格点坐标、编号来源、核对状态和原始信号名称。`sample_box_bindings` 通过接口 ID 关联芯片元件 ID。比特、耦合器、可调 filter 和自定义元件各可关联一个接口，读取线可分别关联 `read_in` 和 `read_out`。删除元件会解除它的样品盒关联，接口本身保留。

打开 v9 或更早的文件会先生成 `.pre-vN.qchip` 备份，再升级至 v10。原有样品盒和接口关联随项目迁移。自定义元件使用 `devices.kind = 'custom'`，用户填写的类型保存到 `subtype`。模板编号由照片刻字及 180° 对称推断得到，核对状态由用户填写。编号和元件名称修改后，接口 ID 保持稳定。

## 自定义模板 JSON

在“样品盒构型”点击“打开模板目录”，将 UTF-8 `.json` 文件放入目录并重启。`id` 使用字母、数字、下划线或连字符，以字母或数字开头。`name` 是选择器中显示的名称。`rows` 从面板正面左上开始逐行排列，每行列数相同。接口编号可以是正整数或非空字符串，每个编号唯一。`0` 或 `null` 表示空位。可用 `inferred` 列出需要显示为推断的接口编号。

```json
{
  "id": "example-4",
  "name": "4 芯示例",
  "rows": [[1, 2], [4, 3]],
  "inferred": [3]
}
```

可选的 `drawing` 对象提供 `origin`（首格中心 `[x, y]`）、`pitch`（格距）、`radius`（接口半径）、`outline`（闭合轮廓顶点列表）、`cover`（盖板 `[x, y, 宽, 高]`）和 `holes`（定位孔或螺钉的 `[x, y, 半径]` 列表）。省略后以网格尺寸绘制矩形面板。内置模板 JSON 位于 `qubit_sequencer/assets/sample_box_templates/`，Windows 安装包内从 `_internal` 进入该路径。自定义目录中的模板可用同一 ID 更新模板选项，已应用的项目继续使用保存的接口和外观。模板读取错误可在界面查看。

## `.qfridge` v3

普通端口 `assignments` 和开关分支 `branch_assignments` 增加可空的 `box_port_id`。有值时以 `sample_id` 和该接口 ID 从关联样品读取当前编号、元件和信号名称。一个样品接口在同一制冷机项目中只能分配给一个端口或分支。

打开 v1 或 v2 的制冷机项目会先生成 `.pre-vN.qfridge` 备份。原分配的 `box_port_id` 留空，继续使用直接关联方式。用户改接样品盒接口时，原引脚、关联器件和迁移资料保留。样品或接口不可用时保留分配，界面和导出显示状态。
