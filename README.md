# PDF 图号替换工具

离线 Windows 桌面工具。用户在图纸上单击图号位置或拖框后，软件对每页同一位置进行 OCR，按首次出现顺序建立新图号，并保证相同原图号使用相同新图号。固定区域完成后，可继续对每页进行文本层和 OCR 全局搜索，按同一映射补充替换其他位置的旧图号。

## 使用流程

1. 打开 PDF，在图号文字上单击；复杂标题栏可直接拖框，仅框住图号所在行。
2. 设置新图号模板。默认值为 `00QK0188RJB-LJ-{seq:03d}-0-260715`。
3. 点击“识别各页图号”，在右侧检查结果。双击某一页可修正 OCR 值，映射会立即重算。
4. 点击“执行替换”并选择输出位置。
5. 输出目录中会生成修改后的 PDF 和同名 `.log`。日志包含逐页识别、完整映射、全局命中、字体策略、字号回退和警告，不生成 CSV。

软件完全离线运行。RapidOCR、ONNX Runtime、PDF 引擎、模型和 Python 运行时均包含在 Windows 便携包内。

## 字体处理

- 对当前这类 CAD Type 3 图纸，使用 pikepdf 直接编辑 PDF 内容流，复用原嵌入字形、字号、文字矩阵和字距。缺少的 `Q/K/J` 字形使用匹配的工程单线字形补入字体子集。
- 对普通 PDF，先尝试文本层和原样式定位，再使用 PyMuPDF 写入。目标字符不在嵌入字体中时会使用内置回退字体，并把字体和字号策略写入日志。
- 任意 PDF 若未嵌入目标字符，数学上无法保证得到完全相同的原字体。软件不会隐瞒这种情况；日志中的“精确保留嵌入字体”字段会明确标识。

## Windows 便携包

仓库的 `Build Windows portable` GitHub Actions 工作流会在每次推送到 `main` 或手动触发时构建：

- 版本号取当前提交日期，格式 `YYYYMMDD`，例如 `20260722`。
- Windows 文件版本使用等价的 `YYYY.M.D.0`，产品版本显示 `YYYYMMDD`。
- Release 标签为 `YYYYMMDD`，同一天重复构建会更新该 Release。
- 下载文件为 `EditNum-YYYYMMDD-win64.zip` 和 SHA-256 文件。
- 解压后直接运行 `EditNum.exe`，无需管理员权限或安装环境。

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

## 仓库结构

```text
.
├── src/drawing_number_tool/   # GUI、CLI、OCR 与 PDF 处理核心
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

许可信息见 `docs/THIRD_PARTY_NOTICES.md`。由于使用 PyMuPDF，分发本项目构建产物时需遵守 AGPL-3.0，或另行取得 Artifex 商业许可。
