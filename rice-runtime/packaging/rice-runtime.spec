# -*- mode: python ; coding: utf-8 -*-
"""One-folder rice-runtime bundle consumed by electron-builder.

The one-folder layout starts faster than PyInstaller one-file and lets the
outer Electron installer sign/package the interpreter and native libraries as
ordinary application resources.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_entry_point, collect_submodules


runtime_root = Path(SPECPATH).parent
package_root = runtime_root / "src"
entrypoint = runtime_root / "packaging" / "runtime_entry.py"

datas = collect_data_files("rice_runtime")
hiddenimports = collect_submodules("rice_runtime")

# keyring discovers platform backends through entry points. Without their
# metadata/hidden imports a frozen build starts, but credential storage fails
# only when a user connects a provider or connector.
keyring_datas, keyring_hiddenimports = collect_entry_point("keyring.backends")
datas += keyring_datas
hiddenimports += keyring_hiddenimports

a = Analysis(
    [str(entrypoint)],
    pathex=[str(package_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name="rice-runtime",
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="rice-runtime",
)
