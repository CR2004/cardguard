"""Start one local Flower SuperNode with the venv on PATH (it spawns app processes by name)."""
import os, sys
VENV_BIN = os.path.dirname(os.path.abspath(sys.executable))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardguard import dotenv as _dotenv  # noqa: E402
_dotenv.load()
os.environ["PATH"] = VENV_BIN + os.pathsep + os.environ.get("PATH", "")
from flwr.supernode.cli import flower_supernode  # noqa: E402
sys.argv = ["flower-supernode", "--insecure", "--superlink", "127.0.0.1:9092",
            "--node-config", "partition-id=0", "--health-server-address", "127.0.0.1:4300"]
sys.exit(flower_supernode())
