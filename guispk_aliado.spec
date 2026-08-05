# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller guispk_aliado.spec
# La app aliada NO incluye sparky_bc (excluida explicitamente): a Impala llega
# unicamente por pyodbc + el DSN corporativo del aliado (SELECT/INSERT sobre
# las tablas de coordinacion guispk_*).

a = Analysis(
    ['main_aliado.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pyodbc'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['sparky_bc', 'interno', 'tests'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='guispk_aliado',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
