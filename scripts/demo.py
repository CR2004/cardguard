"""One command for the demo (`make demo`, `make demo-flower`, `make stop`).

    python scripts/demo.py                 # bank + merchant, decisions in-process
    python scripts/demo.py --flower        # also a local Flower SuperLink and SuperNode; decisions run as AgentApps
    python scripts/demo.py --stop          # stop a demo left running (pid file, then this demo's ports)

Configuration: .env first (existing variables win), then whatever is still missing is read from local credential
files in CARDGUARD_CREDENTIALS (default ~/credentials) and mapped to the names CardGuard reads. Values are never
printed. The Stripe secret key stays in the backend processes; only the publishable key reaches Stripe.js (/config).
Every process runs in its own process group; Ctrl+C, or any process exiting, stops them all.
"""
from __future__ import annotations

import io
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cardguard import dotenv  # noqa: E402
from cardguard.payment_processing.stripe_processor import browser_publishable_key  # noqa: E402

PY = sys.executable
RUN = ROOT / ".demo" / "run"            # pid file and Flower logs (git-ignored)
FLWR_HOME = ROOT / ".demo" / "flwr"     # Flower CLI home holding the local-agent connection
URL = "http://127.0.0.1:4242"
DEMO_PORTS = {4242: "merchant", 4243: "bank"}
FLOWER_PORTS = {8010: "SuperLink", 9092: "SuperLink", 4300: "SuperNode", 9094: "SuperNode"}
OURS = ("cardguard", "run_demo.py", "run_superlink.py", "run_supernode.py", "flower-super", "flwr")

# credential file -> {name inside the file: variable CardGuard reads}
CREDENTIALS = {
    "stripe_test.txt": {"SECRET_API_KEY": "STRIPE_SECRET_KEY", "PUBLISHABLE_KEY": "STRIPE_PUBLISHABLE_KEY"},
    "jev.txt": {"API_KEY": "TYPESAFE_API_KEY"},
    "flower.txt": {"FLOWER_API_KEY": "FLWR_MODEL_API_KEY"},
}


def fail(msg: str) -> None:
    print(f"\nCannot start the demo: {msg}", file=sys.stderr)
    sys.exit(2)


def read_pairs(path: Path) -> dict[str, str]:
    pairs = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            pairs[key.strip()] = value.strip().strip("'\"")
    return pairs


def load_credentials() -> None:
    dotenv.load()
    folder = Path(os.environ.get("CARDGUARD_CREDENTIALS", Path.home() / "credentials")).expanduser()
    loaded = []
    for filename, mapping in CREDENTIALS.items():
        path = folder / filename
        if not path.is_file():
            continue
        pairs = read_pairs(path)
        for src, dst in mapping.items():
            if not os.environ.get(dst) and pairs.get(src):
                os.environ[dst] = pairs[src]
                loaded.append(f"{dst} ({filename})")
    # Direct model calls outside a Flower task use Flower's own endpoint with the same key (.env.example).
    if os.environ.get("FLWR_MODEL_API_KEY") and not (os.environ.get("ENDEAVOR_BASE_URL") or os.environ.get("ENDEAVOR_API_KEY")):
        os.environ["ENDEAVOR_BASE_URL"], os.environ["ENDEAVOR_API_KEY"] = "https://api.flower.ai/v1", os.environ["FLWR_MODEL_API_KEY"]
    print("credentials:", ", ".join(loaded) if loaded else f"none read from {folder} (using .env / the shell)")

    secret, publishable = os.environ.get("STRIPE_SECRET_KEY", ""), os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    if not secret.startswith("sk_test_") or browser_publishable_key(publishable) is None:
        fail("Stripe TEST keys are missing or not TEST keys. Put SECRET_API_KEY=sk_test_... and PUBLISHABLE_KEY=pk_test_... "
             f"in {folder / 'stripe_test.txt'} (or set CARDGUARD_CREDENTIALS to another folder), or set "
             "STRIPE_SECRET_KEY / STRIPE_PUBLISHABLE_KEY in .env. Live keys are refused.")
    for name in ("TYPESAFE_API_KEY", "FLWR_MODEL_API_KEY"):
        if not os.environ.get(name):
            print(f"note: {name} not set; the demo runs without it (rules decide / template explanations)")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def listeners(port: int) -> list[int]:
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def command_of(pid: int) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def stop() -> int:
    """Stop a running demo: its supervisor first (it stops its children), then anything of ours left on its ports."""
    pidfile, stopped = RUN / "demo.pid", []
    if pidfile.exists():
        pid = int(pidfile.read_text() or 0)
        if pid and pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
            for _ in range(60):
                if not pid_alive(pid):
                    break
                time.sleep(0.25)
            stopped.append(f"demo supervisor {pid}")
        pidfile.unlink(missing_ok=True)
    for port in {**DEMO_PORTS, **FLOWER_PORTS}:
        for pid in listeners(port):
            cmd = command_of(pid)
            if any(tag in cmd for tag in OURS):
                os.kill(pid, signal.SIGTERM)
                stopped.append(f"{pid} on :{port}")
            else:
                print(f":{port} is held by a process that is not this demo (pid {pid}); left alone", file=sys.stderr)
    time.sleep(1)
    for port in {**DEMO_PORTS, **FLOWER_PORTS}:
        for pid in listeners(port):
            if any(tag in command_of(pid) for tag in OURS):
                os.kill(pid, signal.SIGKILL)
    print("stopped: " + (", ".join(stopped) if stopped else "nothing was running"))
    return 0


class Supervisor:
    def __init__(self) -> None:
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.stopping = False

    def start(self, name: str, argv: list[str], log: str | None = None) -> None:
        out = open(RUN / log, "w") if log else None  # noqa: SIM115 - held open for the child's lifetime
        proc = subprocess.Popen(argv, cwd=ROOT, env=os.environ.copy(), stdout=out,
                                stderr=subprocess.STDOUT if out else None, start_new_session=True)
        self.procs.append((name, proc))

    def exited(self) -> tuple[str, int] | None:
        for name, proc in self.procs:
            if proc.poll() is not None:
                return name, proc.returncode
        return None

    def stop_all(self, *_signal: object) -> None:
        if self.stopping:
            return
        self.stopping = True
        print("\nstopping the demo ...", flush=True)
        for sig, grace in ((signal.SIGTERM, 8.0), (signal.SIGKILL, 2.0)):
            for _, proc in reversed(self.procs):
                try:
                    os.killpg(proc.pid, sig)  # the whole group: run_demo's bank and merchant, Flower's app processes
                except ProcessLookupError:
                    pass
            deadline = time.time() + grace
            while time.time() < deadline and any(p.poll() is None for _, p in self.procs):
                time.sleep(0.1)
            if all(p.poll() is not None for _, p in self.procs):
                break
        (RUN / "demo.pid").unlink(missing_ok=True)
        print("demo stopped.", flush=True)

    def wait_for(self, what: str, ready, timeout: float) -> None:
        deadline = time.time() + timeout
        while not ready():
            if (gone := self.exited()) is not None:
                self.stop_all()
                fail(f"{gone[0]} exited with code {gone[1]} while starting (see {RUN} for Flower logs)")
            if time.time() > deadline:
                self.stop_all()
                fail(f"{what} did not come up within {timeout:.0f} s (see {RUN} for Flower logs)")
            time.sleep(0.3)


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    args = sys.argv[1:]
    if "--stop" in args:
        return stop()
    flower = "--flower" in args
    stores = args[args.index("--stores") + 1] if "--stores" in args else ""
    RUN.mkdir(parents=True, exist_ok=True)
    if isinstance(sys.stdout, io.TextIOWrapper):  # our lines and the children's in order, even through a pipe
        sys.stdout.reconfigure(line_buffering=True)
    os.environ["PYTHONUNBUFFERED"] = "1"

    pidfile = RUN / "demo.pid"
    if pidfile.exists() and (old := int(pidfile.read_text() or 0)) and pid_alive(old):
        fail(f"a demo is already running (pid {old}). Stop it with: make stop")
    busy = {p: n for p, n in {**DEMO_PORTS, **(FLOWER_PORTS if flower else {})}.items() if port_open(p)}
    if busy:
        fail("ports in use: " + ", ".join(f":{p} ({n})" for p, n in busy.items())
             + ". A demo may still be running: make stop (it only stops this demo's own processes).")
    load_credentials()

    sup = Supervisor()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, sup.stop_all)
    pidfile.write_text(str(os.getpid()))
    demo = [PY, "run_demo.py"] + (["--stores", stores] if stores else [])
    if flower:
        os.environ["FLWR_HOME"] = str(FLWR_HOME)
        config = FLWR_HOME / "config.toml"
        if not config.exists():
            FLWR_HOME.mkdir(parents=True, exist_ok=True)
            config.write_text('[superlink]\ndefault = "local-agent"\n\n[superlink.local-agent]\n'
                              'address = "127.0.0.1:8010"\ninsecure = true\n')
        sup.start("SuperLink", [PY, "scripts/run_superlink.py"], log="superlink.log")
        sup.wait_for("the Flower SuperLink", lambda: port_open(8010) and port_open(9092), 60)
        sup.start("SuperNode", [PY, "scripts/run_supernode.py"], log="supernode.log")
        sup.wait_for("the Flower SuperNode", lambda: port_open(4300), 60)
        print(f"Flower: SuperLink :8010 and SuperNode up (logs in {RUN.relative_to(ROOT)})")
        demo += ["--federation", "local-agent"]
    sup.start("run_demo", demo)
    sup.wait_for("the merchant node", lambda: http_ok(URL + "/config"), 90)
    mode = "Flower local-agent (SuperLink + SuperNode)" if flower else "in-process decisions"
    print(f"\n  CardGuard demo is ready: {URL}   [{mode}]\n  Ctrl+C stops everything.\n", flush=True)
    while not sup.stopping:
        if (gone := sup.exited()) is not None:
            print(f"{gone[0]} exited with code {gone[1]}", file=sys.stderr)
            sup.stop_all()
            return 1
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
