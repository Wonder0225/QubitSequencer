# GitHub 发布

源码仓库包含程序、文档、许可证和模拟示例。Windows 文件夹程序通过 Releases 提供下载。

## 首次发布

1. 选择仓库所有者、仓库名称、公开/私有属性及源码许可。
2. 本机 Git Credential Manager 需要已登录该 GitHub 账号。脚本只在内存中使用登录凭据，不会打印或保存到项目中。
3. 确认 `.gitignore` 已排除实际芯片项目、恢复的数据、缓存和 Windows 构建目录；提交准备好的源码。
4. 确认 `output/release/` 中的最新发布包已经生成，且 `docs/releases/v0.2.1.md` 与实际版本一致。
5. 在项目根目录执行：

```powershell
python scripts/publish_github.py --check-auth
python scripts/publish_github.py --repository QubitSequencer --visibility private
```

当前使用私有仓库。公开发布时将 `private` 改为 `public`；已有仓库的可见性需先在 GitHub 设置中更改，脚本不会自动切换。组织仓库可增加 `--owner 组织名称`，需要账号有相应权限。

脚本创建仓库，推送 `main` 和版本标签，然后先创建草稿 Release；上传 Windows ZIP、使用说明 PDF 和 `SHA256SUMS.txt` 并核对结果后发布。已有仓库中的不同提交和已发布的同名版本不会被覆盖。

## 后续版本

先更新 `pyproject.toml`、程序内版本、Windows 版本资源、构建脚本和版本记录，再添加对应的 `docs/releases/v版本.md`。完成打包后提交源码，按正常 Git 工作流程推送新提交并创建新版本 Release。

首次发布脚本会拒绝覆盖 GitHub 上与本地不同的 `main` 提交；后续版本应先自行推送已审核的新提交，使远端 `main` 与本地一致，再运行脚本上传新版本。

实际 `.qchip` 项目请保存在 `projects/` 或其他发布目录之外。提交前可使用 `git status` 和 `git diff --cached --stat` 查看具体文件。不要提交认证令牌或真实实验数据。
