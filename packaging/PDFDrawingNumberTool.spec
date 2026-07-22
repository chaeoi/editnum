# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


root = Path(SPECPATH).parent
source_root = root / "src"
rapid_datas, rapid_binaries, rapid_hiddenimports = collect_all("rapidocr")
pike_datas, pike_binaries, pike_hiddenimports = collect_all("pikepdf")
ort_binaries = collect_dynamic_libs("onnxruntime")
version_file = root / "build" / "version_info.txt"

a = Analysis(
    [str(source_root / "drawing_number_tool" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=rapid_binaries + pike_binaries + ort_binaries,
    datas=rapid_datas + pike_datas,
    hiddenimports=rapid_hiddenimports + pike_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF图号替换工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(version_file) if version_file.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDF图号替换工具",
)
