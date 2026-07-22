# PDF 批量文字替换工具

离线 Windows 桌面工具。用户在 PDF 中拖出一个文字选区后，软件会识别每页相同位置的内容，按首次出现顺序生成替换文字，并保证相同原文始终使用相同结果。除图号、编号和编码外，也可用于其他结构固定的短文字批量替换。

## 使用流程

1. 打开 PDF，按住鼠标拖出要识别的文字区域。单击不会自动生成选区。
2. 按“固定前段 + 递增序号 + 固定后段”设置替换文字。三段均可留空；递增序号可设置起始值和位数，例如 `001`、`01`、`1`。三段同时为空时清除选中文字。
3. 选择自动匹配原字体，或直接选择本机已安装的 Windows 字体。
4. 点击“识别各页选区”，在右侧检查结果。双击某一页可修正识别文字，替换预览会立即重算。
5. 需要替换文档其他位置的相同文字时，勾选“同时替换全文中的相同文字”；该选项默认关闭。
6. 点击“生成替换文件”并选择输出位置。
7. 输出目录中会生成修改后的 PDF 和同名 `.log`。日志包含逐页识别、完整对应关系、全文命中、字体策略、字号调整和警告。

软件完全离线运行。RapidOCR、ONNX Runtime、PDF 引擎、模型和 Python 运行时均包含在 Windows 便携包内。

## 字体处理

- 对当前这类 CAD Type 3 图纸，使用 pikepdf 直接编辑 PDF 内容流，复用原嵌入字形、字号、文字矩阵和字距。缺少的 `Q/K/J` 字形使用匹配的工程单线字形补入字体子集。
- 对普通 PDF，先尝试文本层和原样式定位，再使用 PyMuPDF 写入。目标字符不在嵌入字体中时会使用内置回退字体，并把字体和字号策略写入日志。
- 全文命中会按完整文字行清除并统一重排，避免只替换局部后出现字号、字距和空隙不一致。
- 手动选择 Windows 字体后，选区位置和全文重排均使用该字体；所选字体缺少目标字符时会停止并明确提示。
- 任意 PDF 若未嵌入目标字符，数学上无法保证得到完全相同的原字体。软件不会隐瞒这种情况；日志中的“精确保留嵌入字体”字段会明确标识。

## Windows 便携包

仓库的 `Build Windows portable` GitHub Actions 工作流会在每次推送到 `main` 或手动触发时构建：

- 版本号取当前提交日期，格式 `YYYYMMDD`，例如 `20260722`。
- Windows 文件版本使用等价的 `YYYY.M.D.0`，产品版本显示 `YYYYMMDD`。
- Release 标签为 `YYYYMMDD`，同一天重复构建会更新该 Release。
- 下载文件统一命名为 `PDFTextReplace-YYYYMMDD-win64.zip`，校验文件为同名 `.sha256`。
- 解压后直接运行 `PDFTextReplace.exe`，无需管理员权限或安装环境。程序目录顶层只包含可执行文件、`runtime` 和 `licenses`，不附带开发文档。

在 GitHub 仓库的 **Releases** 页面直接下载便携包。

## 本地开发

```bash
python -m venv .venv
.venv/bin/pip install -r requirements/build.txt
.venv/bin/python -m drawing_number_tool
.venv/bin/python -m pytest -q
```

命令行模式需要传入归一化区域：

```bash
.venv/bin/python -m drawing_number_tool.cli input.pdf --rect 0.84,0.86,0.995,0.92
```

命令行使用 `--prefix`、`--start`、`--digits`、`--suffix` 设置相同规则；`--no-sequence` 可关闭递增段，`--font-file` 可指定字体文件，`--replace-all` 可启用全文替换。

## 仓库结构

```text
.
├── src/drawing_number_tool/   # GUI、CLI、OCR 与 PDF 处理核心（保留兼容模块名）
├── tests/                     # 单元测试和通用 PDF 回归测试
├── packaging/                 # Windows PyInstaller 配置
├── requirements/              # 运行及构建依赖锁定
├── docs/                      # 第三方组件与许可说明
├── .github/workflows/         # Windows 便携包 CI
├── pyproject.toml             # Python 项目与工具配置
└── README.md
```

## 上游项目

- [pikepdf](https://github.com/pikepdf/pikepdf) 与 [qpdf](https://github.com/qpdf/qpdf)：底层 PDF 内容流处理。
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF)：页面渲染、文本搜索、通用 PDF 写入。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 与 [ONNX Runtime](https://github.com/microsoft/onnxruntime)：离线 OCR。
- [PyInstaller](https://github.com/pyinstaller/pyinstaller)：Windows 便携运行环境打包。

许可信息见 `docs/THIRD-PARTY-NOTICES.txt`。由于使用 PyMuPDF，分发本项目构建产物时需遵守 AGPL-3.0，或另行取得 Artifex 商业许可。
