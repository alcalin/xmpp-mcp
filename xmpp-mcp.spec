# PyInstaller spec — builds a one-file Windows executable: dist\xmpp-mcp.exe
#
#   pip install pyinstaller
#   pyinstaller xmpp-mcp.spec
#
# slixmpp loads its XEP plugins dynamically and fastmcp/mcp/pydantic pull in
# submodules that static analysis misses, so we collect each package wholesale.

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

_datas = []
_binaries = []
_hiddenimports = []

for _pkg in ("slixmpp", "fastmcp", "pydantic", "pydantic_settings", "httpx"):
    _d, _b, _h = collect_all(_pkg)
    _datas += _d
    _binaries += _b
    _hiddenimports += _h

# `mcp` ships an optional Typer-based CLI we don't use and don't depend on;
# importing mcp.cli during collection fails, so collect everything but it.
_datas += collect_data_files("mcp")
_hiddenimports += collect_submodules(
    "mcp", filter=lambda name: not name.startswith("mcp.cli")
)

a = Analysis(
    ["src/xmpp_mcp/__main__.py"],
    pathex=["src"],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="xmpp-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # MCP stdio transport requires a console subprocess
)
