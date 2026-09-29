"""Start one local Flower SuperNode with the venv on PATH (it spawns app processes by name).

    python scripts/run_supernode.py                 # node 0: the merchant on :4242
    python scripts/run_supernode.py --index 1       # node 1: the merchant on :4252 (one SuperNode per store)
    python scripts/run_supernode.py --merchant http://127.0.0.1:4262 --index 2
Each SuperNode is told its own merchant twice (node config and CARDGUARD_MERCHANT_API), since a run's
config is shared by every node; ports step with the index so several nodes fit on one laptop.
"""
import os, sys
VENV_BIN = os.path.dirname(os.path.abspath(sys.executable))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardguard import dotenv as _dotenv  # noqa: E402
_dotenv.load()
os.environ["PATH"] = VENV_BIN + os.pathsep + os.environ.get("PATH", "")


def node_args(index: int = 0, merchant: str | None = None) -> tuple[list[str], str]:
    merchant = merchant or f"http://127.0.0.1:{4242 + 10 * index}"
    return (["flower-supernode", "--insecure", "--superlink", "127.0.0.1:9092",
             "--node-config", f'partition-id={index} merchant-api="{merchant}"',
             "--port", str(9094 + index), "--health-server-address", f"127.0.0.1:{4300 + index}"], merchant)


if __name__ == "__main__":
    argv = sys.argv[1:]
    index = int(argv[argv.index("--index") + 1]) if "--index" in argv else 0
    merchant = argv[argv.index("--merchant") + 1] if "--merchant" in argv else None
    sys.argv, os.environ["CARDGUARD_MERCHANT_API"] = node_args(index, merchant)
    from flwr.supernode.cli import flower_supernode  # noqa: E402
    sys.exit(flower_supernode())
