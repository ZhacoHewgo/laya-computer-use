"""Bounded live checks. Run with the local demo's planner, fixture server and dedicated browser running."""

import argparse
import datetime
import json
import os
from pathlib import Path
from urllib.parse import urlparse

# Set these before importing Browser Harness, which reads its configuration at import time.
os.environ.setdefault("BU_NAME", "laya-local-8770")
os.environ.setdefault("BU_CDP_URL", "http://127.0.0.1:9334")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("BH_TELEMETRY", "0")
os.environ.setdefault("LAYA_MODEL", "aac6fef/laya-multilingual-mlx")
os.environ.setdefault("TEXT_MODEL_BASE_URL", "http://127.0.0.1:8771/v1")
os.environ.setdefault("TEXT_MODEL", "mlx-community/Qwen3-1.7B-4bit")
os.environ["DECISION_MODEL"] = "laya"
os.environ.pop("BU_CDP_WS", None)
os.environ.pop("BU_BROWSER_ID", None)
for setting in ("BU_CDP_URL", "TEXT_MODEL_BASE_URL"):
    if urlparse(os.environ[setting]).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(f"{setting} must point to a local service for this evaluation")

from examples.flights import goal as flight_goal  # noqa: E402
from examples.flights import verify as verify_flights  # noqa: E402
from laya_ultrafast import Agent  # noqa: E402

DAY = datetime.date(2026, 10, 20)
CASES = {
    "zh": ("http://127.0.0.1:8770/fixture-zh.html",
           "查找2026年10月20日从杭州到上海的单程车票，只看直达。看到匹配车次后停止，不要购买。"),
    "travel": ("http://127.0.0.1:8770/fixture.html?scenario=travel",
               "Find a Design stay in Lisbon with Free cancellation and open Casa Flora."),
    "research": ("http://127.0.0.1:8770/fixture.html?scenario=research",
                 "Open the article about using finite choices to control browser agents."),
    "wikipedia": ("https://en.wikipedia.org/", "Open the article Gödel's incompleteness theorems."),
    "flights": ("https://www.google.com/travel/flights?hl=en", flight_goal(DAY)),
}


def verify(name, browser, page):
    """Checks read the final page independently of the policy's plan or DONE choice."""
    if name == "zh":
        observed = browser.evaluate("""(() => ({
          from: document.querySelector('#from')?.value,
          to: document.querySelector('#to')?.value,
          date: document.querySelector('#date')?.value,
          trip: document.querySelector('#trip')?.value,
          direct: document.querySelector('#direct')?.checked,
          rows: [...document.querySelectorAll('#results article')].map(e => e.innerText)
        }))()""")
        checks = {key: observed.get(key) == value for key, value in {
            "from": "杭州", "to": "上海", "date": "2026年10月20日", "trip": "单程", "direct": True,
        }.items()}
        checks["results"] = len(observed["rows"]) == 3 and all(
            all(word in row for word in ("杭州", "上海", "2026年10月20日", "单程", "直达"))
            for row in observed["rows"]
        )
        return {"passed": all(checks.values()), "checks": checks, "observed": observed}
    if name == "flights":
        return verify_flights(page, DAY)
    text = browser.evaluate("document.body.innerText")
    checks = {
        "travel": {
            "detail": page["title"] == "Casa Flora · Forma",
            "destination": "Destination Lisbon" in text,
            "category": "Your filters: Design" in text,
            "cancellation": "Free cancellation enabled" in text,
        },
        "research": {
            "article": page["title"] == "A browser is a choice, not a conversation · Forma",
            "body": "End of article" in text and "Freshness is part of correctness" in text,
        },
        "wikipedia": {
            "article": "Gödel's incompleteness theorems - Wikipedia" == page["title"],
            "body": "mathematical logic" in text,
        },
    }[name]
    return {"passed": all(checks.values()), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", nargs="+", choices=CASES, default=["zh", "travel", "research"])
    parser.add_argument("--max-steps", type=int, default=24)
    parser.add_argument("--output", default="artifacts/verification/latest")
    args = parser.parse_args()
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for name in args.scenarios:
        agent = None
        result = {"scenario": name, "verified": False}
        try:
            agent = Agent(*CASES[name])
            for _ in range(args.max_steps):
                state = agent.command("tick")
                if state["status"] in {"done", "blocked"}:
                    break
            state = agent.snapshot()
            verification = verify(name, agent.browser, state["page"])
            result.update(
                verified=verification["passed"], status=state["status"], actions=len(state["history"]),
                elapsed_ms=state["elapsed_ms"],
                laya_calls=sum(d["usage"].get("model_calls", 0) for d in state["decisions"]),
                planner_calls=sum(d.get("model_calls", 1) for d in state["text_calls"]),
                verification=verification,
            )
            state["verification"] = verification
            (folder / f"{name}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {error}"
            if agent:
                (folder / f"{name}.json").write_text(json.dumps(agent.snapshot(), ensure_ascii=False, indent=2))
        finally:
            if agent:
                agent.close()
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    (folder / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    if not all(r["verified"] and r.get("status") == "done" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
