"""Run the xmpp-mcp server from source (the `python -m xmpp_mcp` equivalent).

    python run-server.py

Re-execs under the project venv if launched with a bare interpreter, then
starts the FastMCP stdio server. Config comes from the environment / .env
(see .env.example); `start-lab.py` + a matching .env give a ready lab login.

This is a stdio JSON-RPC server: run bare it will sit waiting for a client on
stdin (Ctrl-C to stop). For normal use let your MCP client launch it via
.mcp.json instead — this wrapper is for a quick connect smoke test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _reexec_in_venv() -> None:
    """Re-run under the project venv if launched with a bare interpreter.

    Deps (slixmpp, httpx, fastmcp, …) live in ``.venv``; running this with
    system Python otherwise dies on import. Re-exec once so the script "just
    works" regardless of which interpreter the user typed.
    """
    bindir = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"
    venv_py = HERE / ".venv" / bindir / exe
    if not venv_py.exists():
        return  # no venv — let the original import error guide the user
    if Path(sys.executable).resolve() == venv_py.resolve():
        return  # already in the venv
    # subprocess (not os.execv): on Windows execv spawns a detached process and
    # the parent exits, dropping the child's stdout. A child that inherits our
    # console streams its output live, and we forward its exit code.
    try:
        result = subprocess.run([str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])
    except KeyboardInterrupt:
        # Ctrl-C reaches both processes; the child handles its own shutdown.
        # Swallow it here so the re-exec wrapper doesn't print a second stack.
        sys.exit(0)
    sys.exit(result.returncode)


_reexec_in_venv()

sys.path.insert(0, str(HERE / "src"))

from xmpp_mcp.server import main  # noqa: E402  (after venv re-exec + path setup)

if __name__ == "__main__":
    main()
