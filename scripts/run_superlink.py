"""Start the local Flower SuperLink with the venv on PATH (it spawns flower-superexec by name)."""
import os, sys
VENV_BIN = os.path.dirname(os.path.abspath(sys.executable))
os.environ["PATH"] = VENV_BIN + os.pathsep + os.environ.get("PATH", "")
from flwr.superlink.cli import flower_superlink  # noqa: E402
sys.argv = ["flower-superlink", "--insecure", "--host", "127.0.0.1", "--port", os.environ.get("SUPERLINK_PORT", "8010")]
sys.exit(flower_superlink())
