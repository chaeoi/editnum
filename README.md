# EditNUM

EditNUM 是一款离线 Windows PDF 批量文字识别与替换工具。它可以从用户拖出的区域识别每页文字，按统一规则生成新内容，并保证相同原文始终映射到相同结果。

软件最初面向工程图号、零件号和版本号处理，但替换能力不局限于图号，也适用于结构固定的编号、编码及其他短文字。

## 主要功能

- 在 PDF 预览中按住鼠标拖出识别区域，批量读取所有页面相同位置的文字。
- 使用“固定前段 + 递增序号 + 固定后段”生成替换内容，三个部分都可留空。
- 递增序号支持自定义起始值和位数，例如 `1`、`01`、`001`。
- 相同原文在不同页面重复出现时，始终复用第一次生成的替换结果。
- 可选替换文档其他位置出现的相同文字；该功能默认关闭。
- 支持自动匹配 PDF 原字体，也可选择 Windows 已安装字体。
- 识别结果可逐页检查，双击表格行即可修正。
- 全程离线处理，不上传 PDF、识别文字或替换结果。

## 使用方法

1. 运行 `EditNUM.exe`，点击“打开文件”选择 PDF。
2. 在左侧预览中按住鼠标拖出需要识别的文字区域。单击不会创建选区。
3. 设置固定前段、递增起始值、位数和固定后段。两个固定段默认留空，默认结果为 `001`、`002`……
4. 点击“识别选区文字”，在右侧检查识别文字与替换文字。
5. 如需修改识别结果，双击对应页面并输入正确内容。
6. 如需处理正文或备注中的相同文字，勾选“同时替换全文中的相同文字”。
7. 点击“生成替换文件”，选择输出位置。

处理完成后会生成修改后的 PDF 和同名 `.log` 文件。日志记录逐页识别结果、替换对应关系、全文命中、字体策略、字号调整及警告。

## 生成规则

四个规则字段的组合方式如下：

| 固定前段 | 递增起始 | 位数 | 固定后段 | 前两个结果 |
| --- | ---: | ---: | --- | --- |
| `PART-` | `1` | `3` | `-A` | `PART-001-A`、`PART-002-A` |
| 空 | `1` | `2` | 空 | `01`、`02` |
| `REV-` | 空 | `3` | `A` | `REV-A` |
| 空 | 空 | `3` | 空 | 清除选中文字 |

“位数”只在填写递增起始值时生效。相同识别文字不会重复消耗序号。

## 全文替换

全文替换是执行时的可选项，适合同时修改标题栏、正文引用和备注中的相同编号。

- 对受支持的 CAD Type 3 图纸，EditNUM 直接替换 PDF 内容流中的目标字形，保留周围文字、字号、文字矩阵和字距。
- 对普通文本 PDF，优先使用文本层定位并匹配原有样式。
- 对扫描内容或无法直接编辑的文字，独立文字按完整区域重排；长句中的命中采用局部替换，避免 OCR 误差破坏整句。

## 字体处理

- 默认使用“自动匹配原字体”。目标字符存在于嵌入字体中时，优先复用该字体。
- 当前 CAD Type 3 精确路径会复用原嵌入字形，并为缺失的工程字符补入匹配字形。
- SolidWorks 生成的 CID TrueType / Type 0 PDF 会直接复用原字体编码、字号和基线，并保持原文字中心。
- 也可从界面选择 Windows 已安装字体；所选字体缺少目标字符时，软件会停止并给出明确错误。
- 当 PDF 没有可复用字体或目标字符时，无法保证与原字形完全一致。实际策略和字号变化会写入日志。

## 下载与版本

GitHub Actions 在每次推送到 `main` 或手动触发时构建 Windows 版本：

- Release 标题固定为项目名 `EditNUM`。
- tag 与软件版本号一致，当前格式为提交日期 `YYYYMMDD`。
- 下载文件固定为 `EditNUM.zip`，校验文件为 `EditNUM.zip.sha256`。
- 解压后运行 `EditNUM.exe`，无需安装 Python 或使用管理员权限。
- 发布包顶层只包含 `EditNUM.exe`、`runtime` 和 `licenses`。

同一版本重复构建时，工作流会重新创建对应 Release，不在旧 Release 上追加编辑记录。

## 本地开发

需要 Python 3.10 或更高版本：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements/build.txt
.venv/bin/python -m editnum
.venv/bin/python -m pytest -q
```

Windows PowerShell 将 `.venv/bin/python` 替换为 `.venv/Scripts/python.exe`。

命令行模式示例：

```bash
.venv/bin/python -m editnum.cli input.pdf \
  --rect 0.84,0.86,0.995,0.92 \
  --prefix PART- --start 1 --digits 3 --suffix=-A \
  --replace-all
```

常用参数包括 `--prefix`、`--start`、`--digits`、`--suffix`、`--no-sequence`、`--font-file` 和 `--replace-all`。

## 仓库结构

```text
.
├── src/editnum/              # GUI、CLI、OCR 与 PDF 处理核心
├── tests/                    # 单元测试和 Type 3 回归测试
├── packaging/EditNUM.spec    # Windows PyInstaller 配置
├── requirements/             # 运行及构建依赖
├── docs/                     # 第三方许可说明
├── .github/workflows/        # Windows 构建与 Release 工作流
└── pyproject.toml            # Python 项目配置
```

## 许可说明

EditNUM 使用 pikepdf、qpdf、PyMuPDF、RapidOCR、ONNX Runtime、Pillow 和 PyInstaller。第三方许可见 `docs/THIRD-PARTY-NOTICES.txt`。

由于项目使用 PyMuPDF，分发构建产物时需要遵守 AGPL-3.0，或另行取得 Artifex 商业许可。
