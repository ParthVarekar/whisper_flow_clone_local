# PyInstaller spec for whisper-flow standalone executable.
#
# Build (per-OS — PyInstaller does NOT cross-compile):
#   pip install pyinstaller
#   pyinstaller whisper-flow.spec
#
# Output: dist/whisper-flow (one-file executable) on Linux/macOS,
#         dist/whisper-flow.exe on Windows.
#
# For a GUI-only build (no console window on Windows/macOS), add --windowed:
#   pyinstaller --windowed whisper-flow.spec
#
# NOTE: the C++ backends (whisper-cli, llama-server) and models are NOT bundled.
# Users must still build/install those separately (or use the Docker image).

block_cipher = None

a = Analysis(
    ['whisper_flow/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        # include the example config for reference
        ('config.example.json', '.'),
    ],
    hiddenimports=[
        # Tkinter is auto-detected by PyInstaller's hook; list optional deps
        # so they're included if installed.
        'sounddevice',
        'tomli',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # exclude heavy stdlib modules we don't use to shrink the bundle
        'test', 'tests', 'pytest',
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
    name='whisper-flow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # set False for --windowed GUI-only build
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
