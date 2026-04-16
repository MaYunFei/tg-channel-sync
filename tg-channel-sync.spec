from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path.cwd()

hiddenimports = []
for package_name in [
    "aiogram",
    "pyrogram",
    "tgcrypto",
    "aiosqlite",
    "aiohttp",
    "python_socks",
]:
    hiddenimports.extend(collect_submodules(package_name))

datas = [
    (str(project_root / "static"), "static"),
    (str(project_root / "VERSION"), "."),
]
datas += collect_data_files("pyrogram")
datas += collect_data_files("aiogram")


a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tg-channel-sync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="tg-channel-sync",
)
