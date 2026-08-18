# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller guispk_aliado.spec
# La app aliada llega a Impala por Sparky si esta disponible y cae a pyodbc +
# el DSN corporativo si no (SELECT/INSERT sobre las tablas guispk_*). Por eso
# sparky_bc ya NO se excluye: si esta en el entorno de build, se empaqueta.
# `interno` sigue excluido: el aliado no ejecuta nada del lado interno.
# En un entorno de build sin sparky_bc, PyInstaller lo omite y el .exe sale
# solo-ODBC: el import es perezoso y el fallback lo cubre en runtime.

a = Analysis(
    ['main_aliado.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pyodbc'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['interno', 'tests'],
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
