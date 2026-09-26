"""Evaluate saved plans with real local Laya, without calling a text-model API."""

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from examples import evaluate_local
from laya_ultrafast.model import parse_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans", required=True, type=Path)
    args, evaluation_args = parser.parse_known_args()
    saved = json.loads(args.plans.read_text())
    plans = {evaluate_local.CASES[name][1]: plan for name, plan in saved["plans"].items()}

    def supplied(goal, *_args, **_kwargs):
        meta = {**saved.get("provenance", {}), "model_calls": 0, "latency_ms": 0,
                "source": f"Saved plan: {args.plans}; no text-model API call during evaluation"}
        return parse_plan(deepcopy(plans[goal]), meta)

    with (
        patch.object(sys, "argv", [sys.argv[0], *evaluation_args]),
        patch("laya_ultrafast.laya.plan_goal", side_effect=supplied),
        patch("laya_ultrafast.model.chat_json", side_effect=AssertionError("Text-model calls are disabled")),
    ):
        evaluate_local.main()


if __name__ == "__main__":
    main()
