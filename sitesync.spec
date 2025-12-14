# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

project_root = Path.cwd()
sys.path.insert(0, str(project_root / "src"))

hiddenimports = sorted(
    set(
        collect_submodules("sitesync.plugins")
        + collect_submodules("sitesync.fetchers")
        + collect_submodules("sitesync.ui")
        + collect_submodules("sitesync.reports")
    )
)

a = Analysis(
    ["src/sitesync/__main__.py"],
    pathex=["src", "."],
    binaries=[],
    datas=[
        ("config/default.yaml", "config"),
        ("pyproject.toml", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="sitesync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
