# PyInstaller --onefile recipe for qinsim.exe.
#
# Produces a single self-contained Windows executable with the bundled
# scenarios baked in. The exe lands on a fresh Windows 11 box, gets
# double-clicked, and the rich TUI opens — that is the whole deploy
# story we are optimising for.
#
# Build locally:
#     pyinstaller pyinstaller.spec
# CI builds the same way on tag — see .github/workflows/release.yml.

# -*- mode: python ; coding: utf-8 -*-
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

# pathex= below tells PyInstaller's Analysis where to import from, but
# the spec's top-level Python (this file) runs *before* Analysis takes
# effect — so collect_data_files("qinsim", ...) silently returns an
# empty list because qinsim isn't on sys.path yet. Adding src/ here
# is what makes the YAML data files actually bundle.
sys.path.insert(0, os.path.abspath("src"))


# Bundle every YAML under qinsim/scenarios/ so importlib.resources can
# extract them on first run. Anything else (templates, sample configs)
# would also flow through here if added later.
datas = collect_data_files("qinsim", includes=["scenarios/*.yaml"])
assert datas, "collect_data_files found no scenarios — fix sys.path or spec"


block_cipher = None


a = Analysis(
    ["src/qinsim/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim modules we know we never import — keeps the exe under
        # the "small enough to email" threshold and makes virus
        # scanners less twitchy on a fresh corporate Windows box.
        "tkinter",
        "test",
        "unittest",
        "pydoc_data",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
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
    name="qinsim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
