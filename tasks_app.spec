# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['tasks_app.py'],
    pathex=[],
    binaries=[],
    datas=[('locales', 'locales')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc', 'sqlite3',
        'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtPdf', 'PySide6.QtOpenGL', 'PySide6.QtTest',
        'PySide6.QtSpatialAudio',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='tasks_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
