"""Exercise pagination against an isolated local fixture with real Laya and no planner API."""

import argparse
import json
import threading
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from examples.evaluate_local import Agent


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    handler = partial(QuietHandler, directory=str(Path(__file__).parent / "fixtures"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    goal = "Find and open Memory Without Leaks. Stop on the article body."
    plan = {"requirements": [], "open": "Memory Without Leaks", "finish": "The article body is visible."}
    results = []
    try:
        def supplied(*_args, **_kwargs):
            return deepcopy(plan), {"model_calls": 0}

        with (
            patch("laya_ultrafast.laya.plan_goal", side_effect=supplied),
            patch("laya_ultrafast.model.chat_json", side_effect=AssertionError("Planner API disabled")),
        ):
            for mode in ("normal", "delayed", "cycle", "suggestion"):
                agent = None
                result = {"scenario": mode, "passed": False}
                try:
                    url = f"http://127.0.0.1:{server.server_port}/pagination.html?mode={mode}"
                    agent = Agent(url, goal, screenshots=True)
                    for _ in range(30):
                        state = agent.command("tick")
                        if state["status"] in {"done", "blocked"}:
                            break
                    observed = agent.browser.evaluate("({url:location.href,text:document.body.innerText})")
                    turns = sum(h["action"] == "Next page" for h in state["history"])
                    reached = "article=memory" in observed["url"] and "retained references" in observed["text"]
                    passed = (state["status"] == "blocked" and not reached and turns <= 2) if mode == "cycle" else (
                        state["status"] == "done" and reached and turns == 2
                    )
                    if mode == "suggestion":
                        fills = sum(h["kind"] == "fill" for h in state["history"])
                        passed = state["status"] == "done" and reached and turns == 0 and fills == 1
                        result["fills"] = fills
                    result.update(passed=passed, status=state["status"], page_turns=turns,
                                  actions=len(state["history"]), elapsed_ms=state["elapsed_ms"])
                    (args.output / f"{mode}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
                except Exception as error:
                    result["error"] = f"{type(error).__name__}: {error}"
                finally:
                    if agent:
                        agent.close()
                results.append(result)
                print(json.dumps(result), flush=True)
                (args.output / "summary.json").write_text(json.dumps(results, indent=2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
