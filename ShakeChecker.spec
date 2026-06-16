# PyInstaller build spec for ShakeChecker (onedir).
#   build:  pyinstaller ShakeChecker.spec
#   output: dist/ShakeChecker/  (zip this folder and attach it to a GitHub Release)
#
# Produces a folder with ShakeChecker.exe + everything bundled -- no Python needed
# on the user's machine. paths.py resolves resources from sys._MEIPASS when frozen,
# so calibration.toml and src/data/** must be bundled at those same relative paths.

from PyInstaller.utils.hooks import collect_all

# Bundled read-only resources (must match paths.py: resource_root()/calibration.toml
# and resource_root()/src/data).
datas = [
    ("calibration.toml", "."),
    ("src/data", "src/data"),
]
binaries = []
hiddenimports = []

# RapidOCR ships its ONNX models as package data and pulls in onnxruntime's native
# DLLs; collect_all grabs the models, libs and submodules PyInstaller can't see.
for pkg in ("rapidocr_onnxruntime", "onnxruntime"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["src/app.py"],
    pathex=["src"],  # so the bare-name sibling imports (import paths, ...) resolve
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],  # unused; trims size
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ShakeChecker",
    console=False,  # windowless: overlay only, no console (set True to see the live log)
    icon="assets/shakechecker.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ShakeChecker",
)
