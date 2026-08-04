# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller guispk_interno.spec
# Requiere el entorno con PyQt6, pandas y la libreria interna sparky_bc.

a = Analysis(
    ['main_interno.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tests'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='guispk_interno',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
