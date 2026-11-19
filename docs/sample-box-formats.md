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

## `.qfridge` v4

`component_library` 保存项目元件库条目。选用库条目时将默认参数复制到线路实例。`port_circuits` 继续以端口为键，`components` 是端口共用链，`switch` 是有序分支编号，`switch_component` 可记录开关的温级及参数，`branch_components` 按分支编号保存元件列表。样品分配仍保存在原分配表中。

打开 v3 文件先备份再添加元件库表。原线路无需重写，缺少分支元件列表时视为空，缺少开关温级时显示温级未登记。

## `.qfridge` v5

线路 JSON 中的 `switch` 和 `switch_component` 继续保存第一级开关。可选字段 `branch_switches` 以完整分支路径为键，保存该段末端开关的 `component` 参数和有序 `branches` 编号。`branch_components` 可为任一级的线路段登记元件。样品分配和占用使用末端支路的完整路径，例如 `1/2`。打开 v4 文件先备份，现有的一级开关和分支编号继续有效。

## `.qfridge` v6

可选字段 `merge_switches` 以公共输出编号为键，记录合路开关的 `component` 和接入的末端 `inputs`。合路输入继续使用原路径登记元件，公共输出的元件也保存在 `branch_components`，样品分配与占用登记使用公共输出编号。打开 v5 文件时先备份，原线路保留原布局和分配。

## `.qfridge` v7

端口状态原因保存在 `meta` 的 `status_note:<port_id>` 键下。附件沿用标定附件的三个表，`record_attachments.record_id` 指向物理端口，文件快照和历史版本保存在项目内。旧项目打开时先备份再新增附件表，原端口的原因和附件默认留空。
