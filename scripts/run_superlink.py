"""Start the local Flower SuperLink with the venv on PATH (it spawns flower-superexec by name).

Model provider for AgentApp tasks: FLWR_MODEL_API_KEY (flower.ai -> Profile -> Settings -> API Keys),
optionally FLWR_MODEL_API_ENDPOINT (e.g. Nebius). If the variables are not set, they are read from
.demo/flwr_model_api_key and .demo/flwr_model_api_endpoint (0600, git-ignored), so the demo tooling
can start the SuperLink without the key ever appearing in a shell history or the repo.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardguard import dotenv as _dotenv  # noqa: E402
_dotenv.load()
VENV_BIN = os.path.dirname(os.path.abspath(sys.executable))
os.environ["PATH"] = VENV_BIN + os.pathsep + os.environ.get("PATH", "")
for var, name in (("FLWR_MODEL_API_KEY", "flwr_model_api_key"), ("FLWR_MODEL_API_ENDPOINT", "flwr_model_api_endpoint")):
    path = os.path.join(ROOT, ".demo", name)
    if not os.environ.get(var) and os.path.exists(path):
        with open(path) as f:
            os.environ[var] = f.read().strip()
print("model provider:", "configured" if os.environ.get("FLWR_MODEL_API_KEY") else "NOT configured (explanations fall back to templates)")
from flwr.superlink.cli import flower_superlink  # noqa: E402
sys.argv = ["flower-superlink", "--insecure", "--host", "127.0.0.1", "--port", os.environ.get("SUPERLINK_PORT", "8010")]
sys.exit(flower_superlink())
