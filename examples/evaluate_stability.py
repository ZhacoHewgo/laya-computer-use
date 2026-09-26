"""Repeat fixed public-web tasks with explicit API settings and independent outcome checks.

Example: PYTHONPATH=. uv run --extra local --offline python examples/evaluate_stability.py \
--base-url http://localhost:20128/v1 --model MODEL --prompt-key --output artifacts/stability-run
"""

import argparse
import base64
import getpass
import json
import os
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

BERT = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
CASES = {
    "gutenberg_author": ("https://www.gutenberg.org/ebooks/19002",
                         "Verify this is Alice's Adventures Under Ground by Lewis Carroll. "
                         "Stop when this book's own detail page, title and author are visible."),
    "bert_author": ("https://arxiv.org/", f"Find the paper {BERT} by Jacob Devlin and open its abstract page. "
                    "Stop when the requested title, author and abstract are visible."),
    "bert_en": ("https://arxiv.org/", f"Find the paper {BERT} and open its abstract page. "
                "Stop when its title, authors and abstract are visible."),
    "bert_zh": ("https://arxiv.org/", f"请查找论文《{BERT}》，打开它的摘要页面，确认标题、作者和摘要可见后停止。"),
    "ada_zh": ("https://en.wikipedia.org/", "请找到并打开 Ada Lovelace 的维基百科文章，看到人物正文后停止。"),
    "wikipedia": ("https://en.wikipedia.org/", "Find and open the Wikipedia article about Gödel's incompleteness "
                  "theorems. Stop on the article body."),
    "flights": ("https://www.google.com/travel/flights?hl=en", "Search for one-way flights from Zurich to London "
                "on October 20, 2026, for one adult in economy. Stop when matching flight options are visible. "
                "Do not select or book a flight."),
}


def verify_search_case(name, observed):
    """Checks are task-specific; they are never inputs to the planner or policy."""
    parsed = urlparse(observed.get("url", ""))
    path = unquote(parsed.path).rstrip("/")
    text = " ".join(observed.get("text", "").split()).casefold()
    headings = [" ".join(h.split()).removeprefix("Title:").strip() for h in observed.get("headings", [])]
    if name.startswith("bert_"):
        paper = path.removeprefix("/abs/")
        identity = paper == "1810.04805" or (
            paper.startswith("1810.04805v") and paper.removeprefix("1810.04805v").isdigit()
        )
        checks = {"url": parsed.hostname == "arxiv.org" and path.startswith("/abs/") and identity,
                  "heading": BERT in headings, "authors": "jacob devlin" in text and "kenton lee" in text,
                  "abstract": "bidirectional" in text and "language representation model" in text
                  and "fine-tuned" in text}
    elif name == "gutenberg_author":
        checks = {"url": parsed.hostname == "www.gutenberg.org" and path == "/ebooks/19002",
                  "heading": "Alice's Adventures Under Ground by Lewis Carroll" in headings,
                  "author_field": "carroll, lewis, 1832-1898" in text}
    elif name == "ada_zh":
        checks = {"url": parsed.hostname == "en.wikipedia.org" and path == "/wiki/Ada_Lovelace",
                  "heading": "Ada Lovelace" in headings,
                  "body": "charles babbage" in text and "analytical engine" in text}
    elif name == "wikipedia":
        checks = {"url": parsed.hostname == "en.wikipedia.org" and path == "/wiki/Gödel's_incompleteness_theorems",
                  "heading": "Gödel's incompleteness theorems" in headings,
                  "body": "mathematical logic" in text and "kurt gödel" in text}
    else:
        raise ValueError(f"No verifier for {name}")
    return {"passed": all(checks.values()), "checks": checks, "observed": observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--rescore", type=Path, help="Recheck saved page evidence without browser or API calls")
    parser.add_argument("--model")
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--cases", nargs="+", choices=CASES, default=["bert_en", "bert_zh", "ada_zh", "wikipedia"])
    parser.add_argument("--repeat", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--max-steps", type=int, choices=range(1, 61), default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory already exists; choose a new run directory to preserve evidence")
    if args.rescore:
        return rescore(args.rescore, args.output)
    if not args.base_url or not args.model:
        parser.error("Live runs require --base-url and --model")
    os.environ["TEXT_MODEL_BASE_URL"] = args.base_url
    os.environ["TEXT_MODEL"] = args.model
    os.environ.pop("TEXT_MODEL_REASONING", None)
    old_key = os.environ.get("TEXT_MODEL_API_KEY")
    if args.prompt_key:
        os.environ["TEXT_MODEL_API_KEY"] = getpass.getpass("API key (hidden): ")
    try:
        return run(args)
    finally:
        if args.prompt_key:
            if old_key is None:
                os.environ.pop("TEXT_MODEL_API_KEY", None)
            else:
                os.environ["TEXT_MODEL_API_KEY"] = old_key


def rescore(source, output):
    """Preserve the original outcome; independently recheck recorded page evidence, without a rerun."""
    results = json.loads((source / "summary.json").read_text())
    corrected = []
    output.mkdir(parents=True, exist_ok=False)
    for result in results:
        entry = dict(result)
        path = source / f"{result['scenario']}-{result['repetition']}" / "trace.json"
        if path.exists() and result["scenario"] != "flights":
            state = json.loads(path.read_text())
            observed = state.get("verification", {}).get("observed")
            if observed:
                proof = verify_search_case(result["scenario"], observed)
                entry.update(original_checks=result.get("checks"), original_passed=result["passed"],
                             checks=proof["checks"], verified=proof["passed"],
                             passed=result["status"] == "done" and proof["passed"], source_trace=str(path.resolve()))
        corrected.append(entry)
    (output / "summary.json").write_text(json.dumps(corrected, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": sum(r["passed"] for r in corrected), "total": len(corrected),
                      "source": str(source), "additional_api_calls": 0}))
    return 0 if all(r["passed"] for r in corrected) else 1


def run(args):
    # Import only after explicit configuration; evaluate_local also enforces loopback services.
    from examples.evaluate_local import Agent, verify
    from laya_ultrafast import model

    args.output.mkdir(parents=True)
    (args.output / "tasks.json").write_text(json.dumps({n: CASES[n] for n in args.cases}, ensure_ascii=False, indent=2))
    results = []
    original_post = model.post_json
    for repetition in range(1, args.repeat + 1):
        for name in args.cases:
            folder = args.output / f"{name}-{repetition}"
            folder.mkdir()
            agent, state, requests = None, None, []
            started = time.perf_counter()
            outcome = {"scenario": name, "repetition": repetition, "passed": False, "verified": False}

            def tracked_post(url, key, body):
                record = {"requested_model": body.get("model")}
                requests.append(record)
                response = original_post(url, key, body)
                record.update(returned_model=response.get("model"), usage=response.get("usage", {}))
                return response

            print(f"START {name} {repetition}/{args.repeat}", flush=True)
            try:
                with patch.object(model, "post_json", side_effect=tracked_post):
                    agent = Agent(*CASES[name], screenshots=True)
                    deadline = time.monotonic() + 90
                    for _ in range(args.max_steps):
                        state = agent.command("tick")
                        if state["status"] in {"done", "blocked"} or time.monotonic() > deadline:
                            break
                observed = agent.browser.evaluate("""({url:location.href,title:document.title,
                  headings:[...document.querySelectorAll('h1')].map(e=>e.innerText),text:document.body.innerText})""")
                proof = verify("flights", agent.browser, state["page"]) if name == "flights" else (
                    verify_search_case(name, observed)
                )
                state["verification"] = proof
                outcome.update(status=state["status"], verified=proof["passed"],
                               passed=state["status"] == "done" and proof["passed"], checks=proof["checks"],
                               actions=len(state["history"]), elapsed_ms=state["elapsed_ms"], url=observed["url"],
                               laya_calls=sum(d["usage"].get("model_calls", 0) for d in state["decisions"]))
                if state["status"] not in {"done", "blocked"}:
                    outcome["stop_reason"] = "evaluation_budget"
            except Exception as error:
                outcome.update(status="error", error=f"{type(error).__name__}: {error}")
                if agent:
                    state = agent.snapshot()
            finally:
                if agent:
                    state = state or agent.snapshot()
                    (folder / "trace.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
                    if state["page"].get("screenshot"):
                        (folder / "final.jpg").write_bytes(base64.b64decode(state["page"]["screenshot"]))
                    try:
                        agent.close()
                    except Exception as error:
                        outcome["cleanup_error"] = type(error).__name__
            outcome.update(planner_calls=len(requests), requests=requests,
                           wall_ms=round((time.perf_counter() - started) * 1000))
            results.append(outcome)
            (args.output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps({k: v for k, v in outcome.items() if k != "requests"}, ensure_ascii=False), flush=True)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
