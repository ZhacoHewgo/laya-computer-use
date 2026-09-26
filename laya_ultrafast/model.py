"""TypeSafe decisions (optional cloud backend) and the OpenAI-compatible text model helpers."""

import json
import math
import os
import re
import time
from urllib.parse import urlparse

import httpx

from .questions import GOAL_PLAN, NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def local_endpoint(base):
    return urlparse(base).hostname in {"localhost", "127.0.0.1", "::1"}


def chat_json(system, context):
    """One JSON-mode call to the OpenAI-compatible text model. Local servers need no key."""
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key and not local_endpoint(base):
        raise ValueError("The text model needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    model = os.environ.get("TEXT_MODEL", "inception/mercury-2.5")
    no_reasoning = os.environ.get("TEXT_MODEL_REASONING") == "none"
    if local_endpoint(base):
        # Ollama, LM Studio and mlx_lm speak the plain OpenAI dialect.
        reasoning = {"reasoning_effort": "none"} if no_reasoning else {}
    elif "api.deepseek.com/" in (base + "/"):
        reasoning = {"thinking": {"type": "disabled"}}
    else:
        reasoning = {"reasoning": {"enabled": False}} if no_reasoning else {"reasoning": {"effort": "low"}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key or "local",
        {
            "model": model,
            "max_tokens": 1024,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        },
    )
    content = result["choices"][0]["message"]["content"].strip()
    # Some local servers accept JSON mode but still emit Markdown fences. Remove only wrappers;
    # json.loads must still consume exactly one complete object, never a guessed JSON substring.
    wrapped = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    wrapped = re.sub(r"\s*```$", "", wrapped)
    output = json.loads(wrapped)
    return output, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
        "removed_json_fence": wrapped != content,
    }


def field_text(context):
    try:
        output, meta = chat_json(TEXT_VALUE, context)
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as error:
        if "TEXT_MODEL_API_KEY" in str(error):
            raise
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, meta


def plan_goal(goal, fields=(), attempts=3, *, items=()):
    """Once per task: the values the goal states, the item to open, and the visible finish condition.
    `fields` are the observed field labels, so requirements can name the field that sets them. No site plan."""
    context = {"fields_on_page": list(fields)[:40], "items_on_page": list(items)[:40], "goal": goal}
    started = time.perf_counter()
    responses = []
    for attempt in range(attempts):
        try:
            output, meta = chat_json(GOAL_PLAN, context)
            responses.append({"output": output, "usage": meta.get("usage", {})})
            plan, meta = parse_plan(output, meta)
            if any(r["what"].lower() in {"open", "click", "submit", "field label", "打开", "点击", "提交"}
                   or r["what"] in items and r["what"] not in fields for r in plan["requirements"]):
                raise ValueError("Opening an item is not a form requirement; put its title in open.")
            if not fields and plan["open"] and any(
                r["value"].casefold() == plan["open"].casefold() for r in plan["requirements"]
            ):
                raise ValueError("No form fields are visible. Put the item only in open; requirements is [].")
            item = plan["open"]
            if item in items:
                title = re.sub(r"^(?:view|read|open)\s+", "", item, flags=re.IGNORECASE)
                if title != item:
                    plan["open"] = title
                    meta.setdefault("plan_adjustments", []).append(
                        {"field": "open", "from": item, "to": title, "reason": "observed action prefix"}
                    )
            return plan, {**meta, "model_calls": attempt + 1, "responses": responses,
                          "latency_ms": round((time.perf_counter() - started) * 1000)}
        except ValueError as error:
            if "TEXT_MODEL_API_KEY" in str(error) or attempt == attempts - 1:
                raise
            context["validation_error"] = str(error)


def parse_plan(output, meta):
    try:
        raw = output["requirements"]
        if not isinstance(raw, list) or any(
            not isinstance(r, dict) or set(r) != {"what", "value"}
            or not all(isinstance(r[k], str) and r[k].strip() for k in ("what", "value"))
            for r in raw
        ):
            raise ValueError()
        requirements = [
            {"what": r["what"].strip(), "value": r["value"].strip()}
            for r in raw
        ]
        finish, item = output["finish"], output.get("open")
        if not isinstance(finish, str) or not finish.strip() or len(requirements) > 12:
            raise ValueError()
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError()
        item = item.strip() if item is not None else None
        # A generic results collection is a finish condition, not a concrete article/product to open.
        generic = item and re.fullmatch(
            r"(?:(?:the|matching|search|flight|train|hotel|ticket)\s+)*(?:results?|options?|list)(?:\s+page)?"
            r"|(?:匹配的?|搜索|查询|车次|航班|酒店|车票)*(?:结果|列表)(?:页面|页)?",
            item, re.IGNORECASE,
        )
        if generic and requirements:
            meta = {**meta, "plan_adjustments": [{"field": "open", "from": item, "to": None,
                                                   "reason": "generic results collection"}]}
            item = None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("Goal planner returned no valid plan; no action executed.") from None
    return {"requirements": requirements, "open": item, "finish": finish.strip()}, meta
