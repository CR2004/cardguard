"""Start one CardGuard decision as a Flower AgentApp run and read its verdict back.

Uses the SuperLink Control API the same way `flwr chat` does: build the local FAB, StartRun with a
user prompt (an AgentApp run requires one) and the decision id as a run-config override, then
stream the run's events until the coordinator emits its `cardguard.verdict` event.
Works against any SuperLink connection named in ~/.flwr/config.toml ("local-agent", "supergrid").
"""
from __future__ import annotations

import functools
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from cardguard import ROOT

log = logging.getLogger(__name__)
VERDICT_EVENT = "cardguard.verdict"
DEFAULT_PROMPT = "Decide the pending card payment with the merchant node."


@functools.lru_cache(maxsize=1)
def _local_fab(app_path: str):
    """Build (and cache) the FAB for this repo; the code does not change during a demo."""
    from flwr.cli.chat.chat_local_agent import build_local_agent
    return build_local_agent(Path(app_path))


def _series_file(superlink: str) -> Path:
    d = ROOT / ".demo"
    d.mkdir(exist_ok=True)
    return d / f"series_{superlink}.txt"


def load_series(superlink: str) -> int | None:
    try:
        return int(_series_file(superlink).read_text().strip())
    except (OSError, ValueError):
        return None


def save_series(superlink: str, series_id: int) -> None:
    try:
        _series_file(superlink).write_text(str(series_id))
    except OSError:
        pass


def run_config_string(decision_id: str, overrides: tuple = ()) -> str:
    """One space-separated string, exactly what `flwr run --run-config` passes: Flower's parser keeps
    only the LAST element of a list, so several overrides must travel as one string."""
    return " ".join([f'agent.decision-id="{decision_id}"', *overrides])


def _start_run(stub, superlink: str, decision_id: str, prompt: str, app_path: str, overrides: tuple = ()) -> int:
    from flwr.cli.flower_config import read_superlink_connection
    from flwr.common.config import parse_config_args
    from flwr.common.serde import user_config_to_proto
    from flwr.proto.control_pb2 import StartRunRequest
    from flwr.proto.fab_pb2 import Fab
    conn = read_superlink_connection(superlink)
    local = _local_fab(app_path)
    req = StartRunRequest(
        app_spec="",  # the SuperLink derives the app id from the submitted FAB
        user_prompt=prompt,
        federation=conn.federation or "",
        fab=Fab(hash_str=local.fab_hash, content=local.fab_content),
        override_config=user_config_to_proto(parse_config_args([run_config_string(decision_id, overrides)])),
    )
    series = load_series(superlink)
    if series is not None:
        req.series_id = series  # same series => the coordinator's context.state carries over
    res = stub.StartRun(req)
    if not res.HasField("run_id"):
        raise RuntimeError("SuperLink did not start the run")
    if res.HasField("series_id"):
        save_series(superlink, int(res.series_id))
    return int(res.run_id)


def _events(stub, run_id: int) -> Iterator[tuple[str, dict]]:
    from flwr.cli.chat.chat_app import parse_task_event
    from flwr.proto.control_pb2 import StreamRunEventsRequest
    for res in stub.StreamRunEvents(StreamRunEventsRequest(run_id=run_id)):
        yield parse_task_event(res.task_event)


def _client(superlink: str):
    from flwr.cli.flower_config import read_superlink_connection
    from flwr.cli.utils import init_http_client_from_connection
    return init_http_client_from_connection(read_superlink_connection(superlink))


def decide_over_flower(superlink: str, decision_id: str, timeout: float = 240.0,
                       prompt: str = DEFAULT_PROMPT, app_path: str = str(ROOT), overrides: tuple = (),
                       client: Callable[[str], Any] = _client, start_run=_start_run, events=_events) -> dict | None:
    """Run the coordinator AgentApp for one decision; return its verdict, or None on any failure.
    Never raises: the merchant node falls back to deciding in-process and records that it did."""
    result: dict = {}

    def worker() -> None:
        stub = None
        try:
            stub = client(superlink)
            run_id = start_run(stub, superlink, decision_id, prompt, app_path, overrides) if overrides else \
                start_run(stub, superlink, decision_id, prompt, app_path)
            result["run_id"] = run_id
            for event_type, payload in events(stub, run_id):
                result["last_event"] = event_type
                if event_type == VERDICT_EVENT and isinstance(payload.get("verdict"), dict):
                    result["verdict"] = payload["verdict"]
                    return
                if event_type in {"error", "response.failed"}:
                    result["error"] = payload
                    return
        except Exception as e:  # noqa: BLE001 - any failure means "no verdict from the federation"
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            if stub is not None:
                try:
                    stub.close()
                except Exception:  # noqa: BLE001
                    pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        result["error"] = "timed out waiting for the federation"
    if "verdict" not in result:
        log.warning("no verdict from %s for decision %s: %s (last event: %s); check the SuperLink log",
                    superlink, decision_id, result.get("error"), result.get("last_event"))
    return result.get("verdict")
