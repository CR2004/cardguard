"""Join the network with one command: register a new merchant node for the next federated round.

    python -m cardguard.training.join --name store-d --source H          # a real IEEE-CIS vertical
    python -m cardguard.training.join --name store-d --source travel     # a synthetic merchant
"""
from __future__ import annotations

import argparse
import sys

from cardguard.httpjson import post_json


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--source", required=True, help="W/C/R/H/S (real vertical) or electronics/travel/digital")
    ap.add_argument("--merchant", default="http://127.0.0.1:4242")
    a = ap.parse_args(argv)
    out = post_json(a.merchant + "/agent/join", {"name": a.name, "source": a.source}, timeout=10)
    print(f"{out.get('joined')} joined the network ({out.get('nodes')} nodes). It trains in the next round.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
