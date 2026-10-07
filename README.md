# Qubit Sequencer 芯片登记

用于登记超导量子芯片的逻辑构型、比特/耦合器参数、耦合关系和制冷机接线的本机桌面程序。芯片项目保存为单个 `.qchip` 文件。当前版本为 **0.3.2**。

![科研工作台](docs/images/dark-mode.png)

## 功能

- 顶部项目栏提供新建、打开、备份、全局工作点、导出与主题切换；左侧导航在芯片构型、参数数据表、制冷机接线和参数历史之间切换。
- 芯片构型页提供“查看设计图”按钮，点击后显示设计图，使用“返回构型图”按钮回到逻辑结构图。设计图可在新建时或后续导入，支持任意角度旋转、缩放和平移。在图上标注比特/耦合器名称，拖动调整位置，点击查看参数；图片、角度及标注随项目保存，器件改名同步更新。
- 可扩展正方格子、旋转表面码阵列和自定义构型，支持滚轮缩放、拖动平移及适配芯片。
- 可调/固定 Transmon、直接电容耦合、固定/可调耦合器，以及独立的比特-耦合器物理连接。
- 详情面板和器件弹窗共用编辑草稿；保存更改写入文件，取消更改恢复已保存值，关闭弹窗保留草稿。离开文件时可保存全部、放弃或取消。
- 全部项目编辑支持 Ctrl+Z 撤回、Ctrl+Y / Ctrl+Shift+Z 恢复，也可使用“编辑”菜单。包括器件及连接增删、参数与附件、接线、设计图和共享草稿；对话框中的输入也支持撤回与恢复。当前会话保留最近 100 步，切换项目后清空。
- 带单位的参数、自定义字段、工作点、历史记录及制冷机接线登记。参数和线路操作确认后立即写入文件。
- 标签与参数表支持简单 TeX 上下标；数据表可横向或纵向显示，表头和单元格提示保留完整名称、单位及原值。
- 文件菜单支持最近项目，Ctrl+R 打开可搜索列表；最多保留 20 项，跨启动保留。
- 频率显示名采用 `f_{10}`，图层中比特参数优先、耦合器参数置后；新记录使用北京时间，旧 UTC 历史按同一时刻换算显示和导出。
- 参数登记可附加图片、代码、文本等文件，支持拖入与粘贴图像。附件内嵌至芯片文件，历史页提供预览、编辑、替换、可恢复移除及版本恢复。
- 导出 XLSX、多份 CSV、PNG/SVG 构型图和参数图；明暗主题采用统一矢量图标和清晰的选中、焦点及禁用状态。

## Windows 发布版

从 [GitHub Releases](https://github.com/Wonder0225/QubitSequencer/releases/latest) 下载发布压缩包并完整解压，双击 `QubitSequencer.exe`。请保留 `_internal` 文件夹和 EXE 的相对位置。

当前仓库为私有仓库，源码和发布文件需使用有访问权限的 GitHub 账号查看。打开随包附带的 `examples/surface_d3_complete.qchip` 可查看完整 d=3 模拟项目。实际芯片文件请保存到发布目录以外。

## 启动

要求 Python 3.11 或更新版本。项目目录下运行：

```powershell
python -m pip install -e .
python -m qubit_sequencer
```

直接打开已有文件，或生成并校验芯片：

```powershell
python -m qubit_sequencer gui examples/surface_d3_complete.qchip
python -m qubit_sequencer sample my_chip.qchip --template surface --mode coupler
python -m qubit_sequencer validate my_chip.qchip
```

0.3.2 源码及 Windows 发布版使用 v4 文件格式，包含芯片设计图。旧 v1/v2/v3 文件打开时会自动备份并迁移，也可执行 `python -m qubit_sequencer migrate my_chip.qchip`。原有 Windows 0.3.1 发布版使用 v3，不能打开升级后的项目；如需用原有发布版，使用迁移前的 `.pre-vN.qchip` 备份。本次已更新 Windows 发布包；PDF 保留此前版本，设计图和撤回／恢复操作见 Markdown 使用说明。

## 文档与示例

- [中文使用说明](docs/user-guide.md) · [PDF 使用说明](output/pdf/user-guide.pdf)
- [0.3.2 版本说明](docs/releases/v0.3.2.md) · [版本记录](CHANGELOG.md)
- [科研工作台设计参考](docs/workbench-design-references.md)
- [构型图设计参考](docs/diagram-design-references.md)
- [`.qchip` 格式规范 v4](docs/qchip-format-v4.md) · [v3 格式规范](docs/qchip-format-v3.md) · [旧 v2 格式规范](docs/qchip-format-v2.md)
- [完整 d=3 模拟演示项目](examples/surface_d3_complete.qchip)
- [芯片设计图测试示例](examples/chip_design_release_demo.qchip) · [测试操作说明](examples/chip_design_demo.md)
- [构型图](examples/surface_d3_complete_topology.png) · [比特频率图](examples/surface_d3_complete_qubit_f01.png)
- [明色界面](examples/surface_d3_complete_preview.png) · [纵向数据表](examples/surface_d3_complete_table_vertical.png) · [深色界面](examples/surface_d3_complete_dark_preview.png)
- [GitHub 发布说明](docs/github-publishing.md) · [第三方组件说明](THIRD_PARTY_NOTICES.md)

演示项目内所有参数、工作点和接线都是模拟值。顶部主题按钮和“视图 > 深色模式”均可切换配色；图片导出沿用当前主题。

## 构建 Windows 发布版

```powershell
python -m pip install -e ".[build]"
powershell -File scripts/build_windows_release.ps1
```

发布版固定启动入口为 `output/release/QubitSequencer/QubitSequencer.exe`；最终压缩包使用 `QubitSequencer-<版本>-win64.zip` 命名。`output/release/LATEST.txt` 记录已成功构建的版本、时间和文件路径。新版本成功打包后会替换旧发布目录，请将实际项目另存到该目录之外。

仓库附带中文 PDF。修改文档后可安装 `.[docs]` 并运行 `python scripts/build_user_guide_pdf.py`；此脚本需要 Windows 中文字体。运行 `python scripts/capture_demo.py` 可更新真实界面截图；脚本使用示例文件副本和隔离设置，不修改源示例或个人窗口设置。

软件按单人编辑设计。需要传阅时使用“另存备份”，避免多人同时写同一个 `.qchip`。标定实验记录与附件已有存储接口，完整管理界面将按实际实验需求继续扩展。本地 `projects/`、构建缓存与发布目录不会提交到 Git。

## 许可

源码采用 [MIT 许可](LICENSE)。Windows 发布包中的第三方组件保留各自许可，见 [第三方组件说明](THIRD_PARTY_NOTICES.md) 和 `licenses/third-party/`。
