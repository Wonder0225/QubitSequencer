# 0.3.0 科研工作台设计参考

目标是让芯片构型、参数、接线和历史共用稳定的项目上下文，并在紧凑桌面窗口中保留清晰操作层次。参考资料均为官方来源，核对日期为 2026-10-04；下列界面取舍属于本项目的应用判断。

- [Microsoft Fluent 2 设计原则](https://fluent2.microsoft.design/design-principles)：参考其适应平台和保持专注的原则，用稳定导航、明确焦点、熟悉的原生交互和较少视觉噪声组织桌面工作。
- [Linear 官方产品](https://linear.app/) 与 [Linear Method](https://linear.app/method)：以其集中工作上下文和持续保持产品质量的思路作为布局研究参考，采用项目栏、固定侧导航和内容区。具体尺寸与颜色由本项目独立选择。
- [IBM Carbon 数据表指南](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)：参考其数据表工具栏、紧凑密度、选中行操作、长表头提示和内容对齐建议，用于参数及接线表的可扫描性。

本项目采用 14px 正文、16px 标题、32px 主操作和6px圆角。明色背景/面板为 `#F5F6F8/#FFFFFF`，深色为 `#15181E/#1D222A`；蓝色强调交互，轻边框区分区域。器件角色、有效耦合和热图继续保留科研图形的物理语义。

工作台通过共享草稿表达保存状态，危险操作放在对象菜单并确认；图标由内联 SVG 自行绘制，未引入上述设计系统的代码、字体或图标包。