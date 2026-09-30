# Qubit Sequencer 芯片登记

用于登记超导量子芯片的逻辑构型、比特/耦合器参数、耦合关系和制冷机接线的本机桌面程序。芯片项目保存为单个 `.qchip` 文件。

![深色模式界面](examples/surface_d3_complete_dark_preview.png)

## 功能

- 可扩展正方格子、旋转表面码阵列和自定义构型，支持鼠标缩放及适配芯片。
- 可调/不可调 Transmon、直接电容耦合与固定/可调耦合器，以及独立的比特—耦合器连接。
- 带单位的参数、自定义字段、工作点、历史记录及制冷机接线登记。
- 点击元件弹出编辑窗口；标签与参数表支持简单 TeX 上下标；数据表可横向或纵向显示。
- 导出 XLSX、多份 CSV、PNG/SVG 构型图和参数图；支持深色模式。

## Windows 发布版

下载发布压缩包并完整解压，双击 `QubitSequencer.exe`。请保留 `_internal` 文件夹和 EXE 的相对位置。

通过“文件 > 打开 .qchip”打开随包附带的 `examples/surface_d3_complete.qchip`，查看完整 d=3 模拟项目。实际芯片文件请另存到发布目录以外。

## 启动

要求 Python 3.11 或更新版本。项目目录下运行：

```powershell
python -m pip install -e .
python -m qubit_sequencer
```

已有文件可直接打开：

```powershell
python -m qubit_sequencer gui examples/surface_d3_complete.qchip
```

生成和校验示例文件：

```powershell
python -m qubit_sequencer sample my_chip.qchip --template surface --mode coupler
python -m qubit_sequencer validate my_chip.qchip
```

当前格式为 v2。旧 v1 文件打开时会自动备份并迁移，也可手动执行 `python -m qubit_sequencer migrate my_chip.qchip`。

## 文档

- [中文使用说明](docs/user-guide.md)
- [`.qchip` 格式规范 v2](docs/qchip-format-v2.md)
- [构型图设计参考](docs/diagram-design-references.md)
- [完整 d=3 演示项目（模拟数据）](examples/surface_d3_complete.qchip)
- [演示构型图](examples/surface_d3_complete_topology.png)
- [演示比特频率图](examples/surface_d3_complete_qubit_f01.png)
- [完整界面预览](examples/surface_d3_complete_preview.png)
- [纵向数据表预览](examples/surface_d3_complete_table_vertical.png)
- [深色模式预览](examples/surface_d3_complete_dark_preview.png)
- [中文使用说明 PDF](output/pdf/user-guide.pdf)
- [版本记录](CHANGELOG.md)
- [GitHub 发布说明](docs/github-publishing.md)
- [第三方组件说明](THIRD_PARTY_NOTICES.md)

`examples/surface_d3_complete.qchip` 内的所有参数和接线都是模拟值。通过“视图 > 深色模式”切换界面，导出图片也沿用当前配色。

## 构建 Windows 发布版

在 Windows 上运行：

```powershell
python -m pip install -e ".[build]"
powershell -File scripts/build_windows_release.ps1
```

仓库附带生成好的 PDF 使用说明。如需修改后重新生成，安装 `.[docs]`，再运行 `python scripts/build_user_guide_pdf.py`；此脚本需要 Windows 系统中的中文字体。成功打包后仅保留最新发布版本。

发布版固定启动入口为 `output/release/QubitSequencer/QubitSequencer.exe`，当前压缩包为 `output/release/QubitSequencer-0.2.1-win64.zip`。`output/release/LATEST.txt` 记录版本、构建时间和对应文件路径。新版本打包成功后会替换旧发布版，请将实际编辑的 `.qchip` 项目另存到发布目录之外。

第一版按单人编辑设计。一个 `.qchip` 文件可通过“文件 > 另存备份”复制；编辑时不要让多人同时写同一文件。标定实验记录与附件已有存储接口，完整的标定资料界面将随实际实验需求继续扩展。

本地 `projects/`、构建缓存与发布目录不会提交到 Git；仓库只包含源码、文档和模拟示例。Windows 程序通过 GitHub Releases 分发。

## 许可

本项目源码采用 [MIT 许可](LICENSE)。随 Windows 发布包分发的第三方组件保留各自许可，详见 [第三方组件说明](THIRD_PARTY_NOTICES.md) 和 `licenses/third-party/`。
