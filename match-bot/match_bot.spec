# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Match-Bot GUI.
# Build with: pyinstaller match_bot.spec

a = Analysis(
    ['match_bot/gui/app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'jellyfish',
        'scipy.optimize',
        'yaml',
        'match_bot.core',
        'match_bot.core.config',
        'match_bot.core.data_loader',
        'match_bot.core.fuzzy',
        'match_bot.core.lookup',
        'match_bot.core.matching',
        'match_bot.core.reporting',
        'match_bot.core.standardization',
        'match_bot.scripts.run_matching',
        'match_bot.scripts.generate_lookups',
        'match_bot.scripts.suggest_matches',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['geopandas', 'fiona', 'matplotlib', 'notebook', 'IPython'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Match-Bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Match-Bot',
)
