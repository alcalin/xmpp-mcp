"""Start the Openfire lab and prime it for Claude Code use.

Run before ``claude`` so the XMPP bot can log in:

    python start-lab.py

Idempotent — safe to re-run while the lab is already up. Does:

1. ``docker compose up -d`` on the test compose file.
2. Waits for the container to be healthy.
3. Waits for the XMPP C2S port (5222) and admin console (9090).
4. Runs the admin-console session-login handshake that enables the REST API
   plugin, external components and the Monitoring plugin's archiving — none
   of which Openfire enables by default.
5. Pre-creates the three default rooms (r1/r2/r3) the demos use.

Tear it all down with:

    docker compose -f tests/integration/docker/docker-compose.yml down -v
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE = HERE / "tests" / "integration" / "docker" / "docker-compose.yml"
CONTAINER = "xmpp-mcp-test-openfire-1"


def _reexec_in_venv() -> None:
    """Re-run under the project venv if launched with a bare interpreter.

    Deps (httpx, slixmpp, …) live in ``.venv``; running ``python start-lab.py``
    with system Python otherwise dies on ``import httpx``. Re-exec once so the
    script "just works" regardless of which interpreter the user typed.
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
    result = subprocess.run([str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.exit(result.returncode)


_reexec_in_venv()

sys.path.insert(0, str(HERE / "src"))
sys.path.insert(0, str(HERE))


def step(n: int, total: int, msg: str) -> None:
    print(f"[{n}/{total}] {msg}", flush=True)


def wait_for_health(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["docker", "inspect", CONTAINER,
             "--format", "{{.State.Health.Status}}"],
            capture_output=True, text=True,
        )
        status = r.stdout.strip()
        if status == "healthy":
            return
        time.sleep(1.0)
    raise SystemExit(f"container {CONTAINER} never went healthy")


def main() -> None:
    if not COMPOSE.exists():
        raise SystemExit(f"missing compose file: {COMPOSE}")

    step(1, 4, "docker compose up -d")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "up", "-d"],
        check=True, capture_output=True,
    )

    step(2, 4, "waiting for container health")
    wait_for_health()

    # Import after step 1 so a missing dev install fails with a useful error
    # later in the script rather than at module load.
    from tests.integration.conftest import (
        OpenfireHandle,
        _create_rooms,
        _enable_restapi,
        _wait_for_http,
        _wait_for_rest_api,
        _wait_for_tcp,
    )

    handle = OpenfireHandle()
    step(3, 4,
         f"waiting for XMPP C2S ({handle.host}:{handle.c2s_port}) "
         f"and admin console ({handle.admin_url})")
    _wait_for_tcp(handle.host, handle.c2s_port, timeout=120)
    _wait_for_http(f"{handle.admin_url}/login.jsp", timeout=120)

    step(4, 4, "enabling REST API + Monitoring archive system properties")
    _enable_restapi(handle)
    _wait_for_rest_api(handle, timeout=30)
    try:
        _create_rooms(handle)
        print(f"      rooms ready: {', '.join(handle.room_names)} "
              f"on {handle.muc_service}")
    except RuntimeError as exc:
        if "409" not in str(exc):
            raise
        print(f"      rooms already exist (re-run after `down -v` for fresh state)")

    print()
    print("Lab ready.")
    print(f"  XMPP:        {handle.host}:{handle.c2s_port}  "
          f"(bot@{handle.domain} / botpw)")
    print(f"  Admin/REST:  {handle.admin_url}  "
          f"(admin / adminpw)")
    print()
    print("Start Claude Code in this directory:  claude")


if __name__ == "__main__":
    main()
