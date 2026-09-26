"""Local decisions with Laya: open weights on Apple Silicon (MLX), no decision API.

A 421M typed-decision encoder answers narrow questions well: which field sets X, does this value
satisfy Y, which suggestion matches Z. It does not reliably answer "what should a browser do next?".
So each step asks narrow questions and composes them with rules that hold on any site:
fill the values the goal states, pick what a typed query or an opened control offers,
submit, then check the goal's finish condition. Every target is an observed element.
"""

import os
import re
import time
import unicodedata
from copy import deepcopy
from urllib.parse import urlparse

from .model import plan_goal, validate_choice

DEFAULT_MODEL = "aac6fef/laya-typed-decisions-mlx"
FIELD_ROLES = {"combobox", "textbox", "searchbox", "spinbutton", "checkbox", "radio", "switch"}
TOGGLES = {"checkbox", "radio", "switch"}
TRIP_TYPES = {"one way": "single", "oneway": "single", "单程": "single",
              "round trip": "return", "roundtrip": "return", "return": "return", "往返": "return",
              "multi city": "multi", "multicity": "multi", "多程": "multi"}
NEGATIVE = {"no", "off", "false", "unchecked", "disabled", "without", "none"}
NEGATIVE_CJK = {"关闭", "未选", "禁用", "无需", "无须", "不要", "不需要", "不启用", "不勾选"}
SUBMIT_WORDS = {"search", "submit", "find", "go", "apply", "done", "continue", "next", "confirm", "show"}
SUBMIT_CJK = {"搜索", "提交", "查找", "查询", "应用", "完成", "继续", "下一步", "确认", "显示"}
SEARCH_CJK = {"搜索", "查找", "查询"}
STOP_WORDS = {"the", "and", "for", "with", "from", "find", "open", "stop", "when", "visib", "page", "are", "this"}
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
MONTH_DAY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:uary|ruary|ch|il|e|y|ust|t|tember|ober|ember)?"
    r"\s+(\d{1,2})\b|\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
)
CHINESE_DATE = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
NUMERIC_DATE = re.compile(r"(?:\d{4}\s*[-/.]\s*)?(\d{1,2})\s*[-/.]\s*(\d{1,2})")
MONTHS = {name: i for i, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}
RESULT_TITLE_MARKERS = {"search results", "搜索结果", "查询结果"}
YES_NO = {"yes": "the current value satisfies the requirement", "no": "the current value does not satisfy it"}
# Contrasting page kinds separated finished from unfinished pages far better than a yes/no question.
UNFINISHED = {
    "form": "a search form that still has to be submitted",
    "results": "a list of search results, without the requested item opened",
    "other": "some other page",
}

MAX_RESULT_WAITS = 12  # bounded observation attempts for submission acknowledgement
ITEM_RESULT_GRACE_SECONDS = 5.0
MAX_RESULT_SCROLLS = 24

_MODEL = None


def laya():
    """Load once per process. The first forward pass also compiles Metal kernels, so warm it here."""
    global _MODEL
    if _MODEL is None:
        import laya_mlx

        _MODEL = laya_mlx.load(os.environ.get("LAYA_MODEL", DEFAULT_MODEL))
        _MODEL.system_one("Warm up.", {"q": {"type": "choice", "instructions": "Warm up.", "criteria": ["a", "b"]}})
    return _MODEL


def fold(text):
    """Case-fold text while preserving CJK and de-accenting Latin characters."""
    result = []
    for char in unicodedata.normalize("NFKC", str(text)).lower():
        if char.isascii():
            result.append(char if char.isalnum() else " ")
            continue
        ascii_base = "".join(
            part for part in unicodedata.normalize("NFKD", char)
            if part.isascii() and part.isalnum()
        )
        if ascii_base:
            result.append(ascii_base.lower())
        else:
            result.append(char if char.isalnum() else " ")
    return re.sub(r"\s+", " ", "".join(result)).strip()


def access_barrier(page):
    """Recognize explicit access instructions, not incidental discussion of subscriptions."""
    prompts = page.get("access_prompts", page.get("main_text", page["text"]).splitlines())
    pattern = (r"^(?:(?:please\s+)?(?:subscribe|sign in|log in|register|purchase|buy a subscription)"
               r"\b.{0,90}\b(?:to|before you can)\s+(?:continue\s+)?(?:read|reading|access|view|unlock)\b"
               r"|(?:to|in order to)\s+(?:continue\s+)?(?:read|access|view)\b.{0,90}"
               r"\b(?:subscribe|sign in|log in|register)\b"
               r"|(?:请先?|您需要|需要)?(?:登录|登入|订阅|购买会员|开通会员).{0,30}(?:阅读|查看|访问|解锁|继续))")
    return next((text for text in prompts if re.search(pattern, text.strip(), re.I)), None)


def repair_violations(previous, proposed):
    """Keep accepted non-query values, permitting unambiguous field-label remapping."""
    errors, used = [], set()
    requirements = proposed["requirements"]
    for old in previous["requirements"]:
        query_label = fold(old["what"]) in {"search", "search query", "search terms", "query", "搜索", "查询", "搜索词"}
        item_query = is_search_label(old["what"]) and fold(old["value"]) == fold(previous.get("open") or "")
        if query_label or item_query:
            continue  # Search wording is an operation; the item and its conditions remain protected.
        named = [i for i, r in enumerate(requirements) if fold(r["what"]) == fold(old["what"])]
        candidates = named or [i for i, r in enumerate(requirements) if fold(r["value"]) == fold(old["value"])]
        if (len(candidates) != 1 or candidates[0] in used
                or fold(requirements[candidates[0]]["value"]) != fold(old["value"])):
            errors.append(f"Missing or changed condition: {old['what']} = {old['value']}")
        else:
            used.add(candidates[0])
    if fold(previous.get("open") or "") != fold(proposed.get("open") or ""):
        errors.append("The requested item must not be changed or removed during repair.")
    return errors


def author_matches(requested, entry):
    """Preserve person boundaries; reorder only metadata explicitly describing one person.

    Initials are not expanded: J. Smith is insufficient evidence for John Smith.
    """
    def canonical(value, individual):
        value = re.sub(r"^(?:authors?|作者|著者)\s*[:：]\s*|^by\s+", "", value.strip(), flags=re.I)
        if individual:
            value = re.sub(r",\s*\d{4}\s*[-–]\s*\d{0,4}\s*$", "", value)
            parts = [part.strip() for part in value.split(",")]
            if len(parts) == 2 and all(parts):
                value = parts[1] + " " + parts[0]
        return fold(value)

    wanted = canonical(requested, True)
    text = re.sub(r"^(?:authors?|作者|著者)\s*[:：]\s*", "", entry.get("value", ""), flags=re.I)
    if entry.get("individual") is True:
        return bool(wanted) and wanted == canonical(text, True)
    people = re.split(r"[,;\n]|\s+(?:and|&)\s+", text, flags=re.I)
    return bool(wanted) and any(wanted == canonical(person, False) for person in people)


def words(text):
    result = set()
    for token in fold(text).split():
        runs = CJK_RUN.findall(token)
        for run in runs:
            result.add(run)
            result.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
        latin = CJK_RUN.sub(" ", token)
        for part in latin.split():
            stem = part[:5]
            if (len(part) > 2 or part.isdigit()) and stem not in STOP_WORDS:
                result.add(stem)
    return result


def month_day(text):
    m = MONTH_DAY.search(fold(text))
    if m:
        return MONTHS[(m[1] or m[4])[:3]], int(m[2] or m[3])
    m = CHINESE_DATE.search(str(text))
    if m:
        return int(m[2]), int(m[3])
    m = NUMERIC_DATE.search(str(text))
    return (int(m[1]), int(m[2])) if m else None


def is_negative(text):
    value = fold(text)
    return bool(set(value.split()) & NEGATIVE) or any(word in value for word in NEGATIVE_CJK)


def is_submit_label(text):
    value = fold(text)
    return bool(set(value.split()) & SUBMIT_WORDS) or any(word in value for word in SUBMIT_CJK)


def is_search_label(text):
    value = fold(text)
    return "searc" in words(value) or any(word in value for word in SEARCH_CJK)


def observed(page):
    """One entry per DOM node, indexed in the same order as model.action_space (the inspector's indices)."""
    elements, nodes = [], {}
    for action in page["actions"]:
        if action["kind"] not in {"click", "fill", "select", "enter"}:
            continue
        e = nodes.get(action["node"])
        if e is None:
            e = nodes[action["node"]] = {
                "node": action["node"],
                "index": str(len(elements) + 1),
                "role": action.get("role", ""),
                "label": action["label"].split(" → ")[0].strip(),
                "value": action.get("current_value", action.get("value", "")) or "",
                "checked": action.get("checked"),
                "expanded": action.get("expanded"),
                "hint": action.get("hint", ""),
                "href": action.get("href", ""),
                "pagination_next": action.get("pagination_next", False),
                "result_title": action.get("result_title", ""),
                "result_primary": action.get("result_primary", False),
                "result_context": action.get("result_context", ""),
                "result_authors": action.get("result_authors", []),
                "form": action.get("form"),
                "dialog": action.get("dialog"),
                "is_submit": action.get("is_submit", False),
                "actions": {},
                "options": [],
            }
            elements.append(e)
        if action["kind"] == "select":
            e["options"].append(action)
        else:
            e["actions"].setdefault(action["kind"], action)
    for e in elements:
        if e["role"] in TOGGLES:
            e["current"] = "checked" if e["checked"] in {"true", True} else "unchecked"
        elif e["value"] or (is_field(e) and e["role"] != "button"):
            e["current"] = e["value"]
        else:
            e["current"] = e["label"]
    return elements


def is_field(e):
    # Buttons that display a count ("1 passenger") act as fields; dated buttons are calendar choices.
    counter = e["role"] == "button" and re.search(r"\d", e["label"]) and not month_day(e["label"])
    return e["role"] in FIELD_ROLES or bool(e["options"]) or bool(counter)


def display(e):
    """The name the planner sees and may copy as a requirement's "what"."""
    return f"{e['label'][:80]} ({e['hint'][:60]})" if e.get("hint") else e["label"][:80]


def plannable(e):
    """Elements a requirement can name: fields, and buttons, which often open pickers for dates or counts."""
    return is_field(e) or e["role"] == "button"


def describe(e):
    text = f"{e['role']} {e['label'][:70]}"
    if e.get("result_title"):
        text += f" | Title: {e['result_title']} | {e.get('result_context', '')[:180]}"
    if e.get("hint"):
        text += f" ({e['hint'][:50]})"
    if is_field(e) and e["current"] != e["label"]:
        text += f" = {e['current'][:40] or '(empty)'}"
    if e["options"]:
        text += " (options: " + ", ".join(option_label(o) for o in e["options"][:6]) + ")"
    return text


def option_label(option):
    return option["label"].split(" → ")[-1]


def settled(requirement, e):
    """True or False when plain code can tell; None asks Laya."""
    value = requirement["value"]
    if e["role"] in TOGGLES:
        return (e["current"] == "checked") != is_negative(value)
    current = e["current"]
    if not fold(current):
        return False
    if any(fold(option_label(o)) == fold(value) for o in e["options"]):
        return False  # The requested value is still offered as an unselected option.
    wanted, shown = month_day(value), month_day(current)
    if wanted and shown:
        return wanted == shown
    fv, fc = fold(value), fold(current)
    # Known mutually exclusive values must not be overruled by a semantic yes/no answer.
    if fv in TRIP_TYPES and fc in TRIP_TYPES:
        return TRIP_TYPES[fv] == TRIP_TYPES[fc]
    if fv and (fv in fc or (len(fc) >= 3 and fc in fv)):
        return True
    return None


def summary(text, about, limit=200):
    """The page lines sharing the most words with `about`, in page order. Page chrome comes first on most
    sites, so the opening characters rarely describe the page."""
    lines = [line for line in text.splitlines() if line.strip()]
    target = words(about)
    ranked = sorted(range(len(lines)), key=lambda i: -len(words(lines[i]) & target))
    kept, size = set(), 0
    for i in ranked:
        if size >= limit:
            break
        kept.add(i)
        size += len(lines[i])
    return " | ".join(lines[i] for i in sorted(kept))[: limit * 2]


def location(url):
    """Sites often rewrite the query on every edit; a new host or path means a new page."""
    parsed = urlparse(url)
    return parsed.netloc, parsed.path


def titled(title, name):
    """The page title carries most of the name's words, and they make up most of the title. A search results
    page ("X - Search results - Site") names the item too, but is not its page."""
    folded_title, folded_name = fold(title), fold(name)
    if any(marker in folded_title for marker in RESULT_TITLE_MARKERS):
        return False
    if folded_name and folded_name in folded_title:
        return True
    # Full identity tokens, without the five-character stems used for candidate ranking.
    wanted = set(re.findall(r"[^\W_]+", folded_name)) - {"the", "and", "of"}
    shown = set(re.findall(r"[^\W_]+", folded_title))
    return bool(wanted) and wanted <= shown


def relevance(e, text):
    s = len(words(f"{e['label']} {e.get('hint', '')} {e['current']}") & words(text))
    date = month_day(text)
    return s + 5 if date and month_day(e["label"]) == date else s


def shortlist(candidates, text, limit, bonus=None, top_tier=False, margin=1):
    """Keep the likeliest candidates, in document order, so options fit Laya's 256-token question budget.
    With top_tier, only candidates scoring within `margin` points of the best remain."""
    scores = {e["node"]: relevance(e, text) + (bonus(e) if bonus else 0) for e in candidates}
    if top_tier and candidates:
        best = max(scores.values())
        candidates = [e for e in candidates if scores[e["node"]] >= best - margin]
    kept = {e["node"] for e in sorted(candidates, key=lambda e: -scores[e["node"]])[:limit]}
    return [e for e in candidates if e["node"] in kept]


class LayaPolicy:
    def __init__(self, goal):
        self.goal = goal
        self.plan = None
        self.initial_plan = None
        self.plan_meta = None
        self.fields = {}  # requirement index -> observed node
        self.met = set()
        self.attempts = {}
        self.pending = None  # the latest decision, until history shows it executed
        self.last = None  # the latest executed step
        self.item_step = None  # retain the selected item across read-only loading waits
        self.edit_url = None
        self.submitted = False
        self.waits = 0
        self.seen = 0
        self.tried = {}  # label -> times clicked as the next step
        self.typed = False  # controls changed since the last acknowledged submit
        self.acted = None
        self.search_added = False
        self.frozen = set()  # requirements submitted to an earlier page
        self.failed = {}  # node -> decisions on it that could not execute
        self.before_submit_lines = set()
        self.before_submit_nodes = set()
        self.submit_url = None
        self.awaiting_submit = False
        self.dirty_forms = set()
        self.result_deadline = 0.0
        self.result_scrolls = set()
        self.confirming_dialog = None
        self.satisfied_values = {}
        self.input_step = None
        self.rejected_fields = {}
        self.planning_events = []
        self.repairs = 0
        self.repair_reason = None
        self.repair_violation = []
        self.terminal_reason = None
        self.rejected_urls = set()
        self.backtracks = 0
        self.returning_from = None
        self.ranked_pages = set()
        self.refined_query = False
        self.page_turns = 0
        self.pagination_seen = set()
        self.visited_result_sets = set()
        self.awaiting_page = None

    # Bookkeeping ---------------------------------------------------------------------------------------------

    def sync(self, history):
        step = self.pending
        if step and step["node"] is not None and len(history) == self.seen:
            # The decision never ran: the target was covered or the page changed first.
            self.failed[step["node"]] = self.failed.get(step["node"], 0) + 1
        if len(history) > self.seen and step and history[-1].get("choice") == step["choice"]:
            self.last = step
            if step["kind"] in {"fill", "open"}:
                self.input_step = step
            elif step["kind"] != "wait":
                self.input_step = None
            if step["kind"] == "item":
                self.item_step = step
            elif step["kind"] != "wait":
                self.item_step = None
            if step["kind"] == "submit":
                self.before_submit_lines = step["visible_lines"]
                self.before_submit_nodes = step["before"]
                self.submit_url = step["url"]
                self.awaiting_submit = True
                self.result_deadline = time.monotonic() + ITEM_RESULT_GRACE_SECONDS
                self.result_scrolls.clear()
            if step["kind"] == "page_next":
                self.page_turns += 1
                self.visited_result_sets.add(tuple(step["result_signature"]))
                self.pagination_seen.update((step["url"], step["href"]))
                self.awaiting_page = step
            if step["kind"] == "back":
                self.backtracks += 1
                self.returning_from = step["url"]
            if step["kind"] == "rank_results":
                self.typed = True
                self.submitted = False
                if step.get("form") is not None:
                    self.dirty_forms.add(step["form"])
                self.ranked_pages.add(step["url"])
                self.result_deadline = time.monotonic() + ITEM_RESULT_GRACE_SECONDS
                self.result_scrolls.clear()
            if step["kind"] == "scroll":
                self.result_scrolls.add((step["url"], step["scroll_y"]))
            if step["kind"] == "confirm":
                self.confirming_dialog = step["dialog"]
            if step["req"] is not None:
                self.attempts[step["req"]] = self.attempts.get(step["req"], 0) + 1
                self.edit_url, self.submitted = step["url"], False
            if step["kind"] == "refine_query":
                self.refined_query = True
            if step["kind"] in {"fill", "select", "toggle", "pick", "refine_query"}:
                self.typed = True
                self.submitted = False
                if step.get("form") is not None:
                    self.dirty_forms.add(step["form"])
            if step["kind"] in {"submit", "item", "next"}:
                self.tried[step["label"]] = self.tried.get(step["label"], 0) + 1
            self.waits = self.waits + 1 if step["kind"] == "wait" else 0
            if step["kind"] != "wait":
                self.acted = step["kind"]  # the latest non-wait step
        self.seen, self.pending = len(history), None

    def ask(self, state, questions):
        result = laya().system_one(state, questions)
        self.model_calls += 1
        for key, answer in result["answers"].items():
            if questions[key]["type"] == "choice":
                try:
                    validate_choice(answer, questions[key]["criteria"])
                except ValueError:
                    raise ValueError("Invalid Laya response; no action executed.") from None
        self.answers.update(result["answers"])
        self.questions.update(questions)
        self.tokens += result["usage"]["input_tokens"]
        return result["answers"]

    def pick(self, qid, candidates, state, instructions, text, limit=20, bonus=None, allow_none=False,
             top_tier=False, margin=1):
        date = month_day(text)
        dated = [e for e in candidates if date and month_day(e["label"]) == date]
        if len(dated) == 1:
            return dated[0], None  # An exact calendar match needs no model call; Laya confused adjacent days.
        ranked = shortlist(candidates, text, limit, bonus, top_tier, margin)
        if not ranked:
            return None
        criteria = {str(e["node"]): describe(e) for e in ranked}
        if allow_none:
            criteria["none"] = "none of these"
        answer = self.ask(state, {qid: {"type": "choice", "instructions": instructions, "criteria": criteria}})[qid]
        if answer["choice"] == "none":
            return None
        return next(e for e in ranked if str(e["node"]) == answer["choice"]), answer

    # Decisions ----------------------------------------------------------------------------------------------

    def make_plan(self, page, elements, reason=None, history=()):
        labels = list(dict.fromkeys(display(e) for e in elements if is_field(e)))
        items = list(dict.fromkeys(
            e["label"] for e in elements if "click" in e["actions"] and not is_field(e)
            and not e["is_submit"] and (e["role"] == "link" or not is_submit_label(e["label"]))
        ))
        controls = [{"label": display(e), "role": e["role"], "value": e["current"],
                     "operations": list(e["actions"]), "options": [option_label(o) for o in e["options"]]}
                    for e in elements if is_field(e)]
        plan, meta = plan_goal(self.goal, labels, items=items, controls=controls,
                              page={"url": page["url"], "title": page["title"], "text": page["text"][:4000],
                                    "results": [{"title": e["result_title"], "href": e["href"],
                                                 "context": e["result_context"]}
                                                for e in elements if e.get("result_title")][:10]},
                              feedback={"reason": reason, "history": list(history)[-8:],
                                        "accepted_plan": deepcopy(self.plan)} if reason else None)
        self.repair_violation = []
        if reason:
            self.repairs += 1
            self.repair_violation = repair_violations(self.plan, plan)
            if self.repair_violation:
                self.planning_events.append({**meta, "field": "rejected goal repair", "value": deepcopy(plan),
                                             "reason": self.repair_violation, "accepted": False})
                return
            plan["finish"] = (self.initial_plan or self.plan)["finish"]
            # A repair must not silently drop identity conditions from the accepted initial plan.
            for key in ("identity_terms", "authors"):
                retained = list(dict.fromkeys((self.initial_plan or {}).get(key, []) + self.plan.get(key, [])))
                if retained:
                    plan[key] = list(dict.fromkeys(retained + plan.get(key, [])))
            # Re-map from the current page. Never replay a previous mutation or erase action history.
            self.fields.clear()
            self.met.clear()
            self.frozen.clear()
            self.attempts.clear()
            self.satisfied_values.clear()
            self.rejected_fields.clear()
            self.input_step = None
            self.search_added = False
            self.waits = 0
        else:
            self.initial_plan = deepcopy(plan)
        self.plan, self.plan_meta = plan, meta
        self.planning_events.append({**meta, "field": "goal repair" if reason else "goal plan",
                                     "value": deepcopy(plan), "reason": reason})

    def choose(self, page, history):
        started = time.perf_counter()
        self.sync(history)
        elements = observed(page)
        if self.plan is None:
            self.make_plan(page, elements)
        elif self.repair_reason and self.repairs < 2 and self.initial_plan is not None:
            self.make_plan(page, elements, self.repair_reason, history)
        self.repair_reason = None
        self.answers, self.questions, self.tokens = {}, {}, 0
        self.model_calls = 0
        if self.repair_violation:
            result = ("BLOCKED", None, None, None, "blocked", None, None)
        else:
            result = self.decide(page, elements)
        op, element, action, picked, kind, req, text = result
        if op == "BLOCKED" and not self.terminal_reason and self.repairs < 2 and self.initial_plan is not None:
            self.repair_reason = str(self.repair_violation) if self.repair_violation else (
                f"No executable progress. Requirement index: {req}; current plan: {self.plan}")
            op, kind = "WAIT", "wait"
        answer = picked[1] if picked else None
        indices = {str(e["node"]): e["index"] for e in elements}
        choice = action["id"] if action else {"DONE": "DONE", "BLOCKED": "BLOCKED"}.get(op, "wait")
        probability = answer["probabilities"][answer["choice"]] if answer else None
        target = element["index"] if element else None
        if action and action["kind"] == "select":
            target = f"{element['index']}:{element['options'].index(action) + 1}"
        self.pending = {
            "choice": choice, "kind": kind, "req": req, "node": element["node"] if element else None,
            "result_signature": self.result_signature(elements),
            "href": element.get("href") if element else None,
            "url": page["url"], "before": {e["node"] for e in elements},
            "label": element["label"] if element else None,
            "visible_lines": {line.strip() for line in page["text"].splitlines()},
            "form": element.get("form") if element else None,
            "dialog": element.get("dialog") if element else None,
            "role": element.get("role") if element else None,
            "scroll_y": page.get("scroll", {}).get("y", 0),
        }
        return {
            "choice": choice,
            "operation": op,
            "target": target,
            "text": text,
            "confidence": answer["confidence"] if answer else None,
            "target_source": "laya" if answer else "rule",
            "probabilities": {choice: probability},
            "operation_probabilities": {},  # Rules compose operations; target scores are not operation scores.
            "target_probabilities": {
                indices.get(k, k): p for k, p in (answer or {}).get("probabilities", {}).items() if k in indices
            },
            "target_confidence": answer["confidence"] if answer and element else None,
            "raw_answers": self.answers,
            "model": os.environ.get("LAYA_MODEL", DEFAULT_MODEL),
            "usage": {"input_tokens": self.tokens, "output_tokens": 0, "model_calls": self.model_calls},
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "request": {"plan": deepcopy(self.plan), "questions": self.questions},
            "stop_reason": self.terminal_reason or ("; ".join(self.repair_violation) or None),
        }

    @staticmethod
    def result_signature(elements):
        return sorted({(e.get("result_title", ""), e.get("href", ""))
                       for e in elements if e.get("result_title")})

    def decide(self, page, elements):
        """Return (operation, element, action, (element, answer) or None, step kind, requirement, text)."""
        barrier = access_barrier(page)
        if barrier:
            self.terminal_reason = "Content access requires login or subscription: " + barrier[:180]
            return "BLOCKED", None, None, None, "blocked", None, None
        reqs = self.plan["requirements"]
        by_node = {e["node"]: e for e in elements}
        clickable = [e for e in elements if "click" in e["actions"] and self.failed.get(e["node"], 0) < 2]
        last = self.last
        if self.awaiting_page:
            previous = self.awaiting_page
            signature = self.result_signature(elements)
            if not signature or (page["url"] == previous["url"] and signature == previous["result_signature"]):
                op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
                return op, None, None, None, op.lower(), None, None
            if tuple(signature) in self.visited_result_sets:
                return "BLOCKED", None, None, None, "blocked", None, None
            self.awaiting_page = None
            self.result_scrolls.clear()
            self.result_deadline = time.monotonic() + ITEM_RESULT_GRACE_SECONDS
        if self.returning_from:
            if page["url"] == self.returning_from:
                op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
                return op, None, None, None, op.lower(), None, None
            self.returning_from = None

        # Search suggestions can be navigation links, not merely values for a form field.
        # Once such a click lands on the requested item, keep its search requirement satisfied.
        if (last and last["kind"] == "pick" and last.get("role") == "link"
                and last.get("req") is not None and page["url"] != last["url"]
                and self.plan.get("open") and titled(page["title"], self.plan["open"])):
            self.met.add(last["req"])
            self.frozen.add(last["req"])
            self.typed = False
            self.dirty_forms.clear()
            self.input_step = None

        # Confirming a picker is a substep, not submission of the whole search form.
        # Never replay its confirmation while waiting for the dialog to close.
        if self.confirming_dialog is not None:
            if any(e["dialog"] == self.confirming_dialog for e in elements):
                op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
                return op, None, None, None, op.lower(), None, None
            self.confirming_dialog = None

        if last and last["kind"] == "pick" and last.get("dialog") is not None and last["req"] is not None:
            requirement = reqs[last["req"]]
            selected = {"role": "option", "current": last["label"], "options": []}
            confirms = [e for e in clickable if e["dialog"] == last["dialog"] and e["role"] == "button"
                        and re.match(r"^(?:done|confirm|apply|确定|确认|应用|完成)(?:$|\s)", fold(e["label"]))]
            if settled(requirement, selected) is True and len(confirms) == 1:
                return self.click((confirms[0], None), "confirm", None)

        if self.awaiting_submit:
            # A click is not acknowledgement. Wait for read-only evidence before any further mutation.
            wanted = set().union(*(words(r["value"]) for r in reqs)) if reqs else set()
            new_lines = {line.strip() for line in page["text"].splitlines()} - self.before_submit_lines
            applied = page["url"] != self.submit_url or any(
                words(line) & wanted for line in new_lines
            )
            if not applied:
                op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
                return op, None, None, None, op.lower(), None, None
            self.awaiting_submit, self.typed, self.submitted = False, False, True
            self.dirty_forms.clear()
            if location(page["url"]) != location(self.submit_url):
                self.frozen |= self.met

        # In-place search can acknowledge submission before a later item navigation changes the URL.
        # Its completed search requirements belong to the old page, not the item's unrelated fields.
        if self.submitted and self.submit_url and location(page["url"]) != location(self.submit_url):
            self.frozen |= self.met

        # 1. A typed query or an opened control offers new choices: take the one its requirement asks for.
        editing = self.input_step or (last if last and last["kind"] in {"fill", "open"} else None)
        if editing and editing["req"] is not None:
            r = reqs[editing["req"]]
            new = [e for e in clickable if e["node"] not in editing["before"]]
            # Suggestions name the value; controls that appear beside them ("Clear", "Swap") do not.
            new = [e for e in new if relevance(e, r["value"])]
            state = f"Requirement: {r['what']} = {r['value']}"
            picked = self.pick("option", new, state, f"Which option sets {r['what']} to {r['value']}?",
                               r["value"], allow_none=True)
            if picked:
                return self.click(picked, "pick", editing["req"])
            current = by_node.get(editing["node"])
            if editing["kind"] == "fill" and editing.get("role") == "combobox" and (
                current is None or current["expanded"] in {True, "true"}
            ):
                op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
                return op, None, None, None, op.lower(), None, None

        navigation = self.plan.get("navigate")
        if navigation:
            if self.last and self.last["kind"] == "navigate":
                return "BLOCKED", None, None, None, "blocked", None, None
            targets = [e for e in clickable if e["label"] == navigation and not is_field(e)
                       and not e["is_submit"] and (e["role"] == "link" or not is_submit_label(e["label"]))]
            if len(targets) == 1:
                e = targets[0]
                return "CLICK", e, e["actions"]["click"], None, "navigate", None, None
            return "BLOCKED", None, None, None, "blocked", None, None

        # An item to open that nothing on the page names has to be searched for first.
        item = self.plan.get("open")
        identity_terms = self.plan.get("identity_terms", [])

        authors = self.plan.get("authors", [])

        def matches_authors(evidence):
            return all(any(author_matches(author, entry) for entry in evidence) for author in authors)

        def matches_identity(text):
            normalized = " " + fold(text) + " "
            return all(" " + fold(term) + " " in normalized for term in identity_terms)
        if item and not self.search_added and not titled(page["title"], item):
            search = [e for e in elements if "fill" in e["actions"] and
                      (e["role"] == "searchbox" or is_search_label(e["label"]))]
            planned_search = any(
                is_search_label(r["what"]) or any(fold(r["what"]) in {fold(e["label"]), fold(display(e))}
                                                  for e in search) for r in reqs
            )
            if search and not planned_search and not any(relevance(e, item) for e in clickable):
                reqs.append({"what": "search", "value": item})
                self.search_added = True

        # 2. Requirements in the goal's order: map each to an observed element, then check its value.
        if set(range(len(reqs))) - self.met and not any(plannable(e) for e in elements):
            # Nothing to fill yet: the page is still loading or showing an interstitial. This costs no attempt.
            op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
            return op, None, None, None, op.lower(), None, None
        self.refresh(page, elements, by_node)
        for i, r in enumerate(reqs):
            if i in self.met:
                continue
            if self.attempts.get(i, 0) >= 3:
                return "BLOCKED", None, None, None, "blocked", i, None
            e = by_node.get(self.fields.get(i))
            state = f"Requirement: {r['what']} = {r['value']}"
            if e is None:
                # A control about this requirement that already displays its value ("Travellers and cabin
                # class: 1 Adult, Economy") settles it. Both the name and the value must appear.
                # Calendar days name a date too, but they are choices, not displays.
                shown = [c for c in elements if plannable(c) and c["role"] not in TOGGLES and not month_day(c["label"])
                         and words(c["label"]) & words(r["what"]) and words(c["label"]) & words(r["value"])
                         and settled(r, {**c, "current": c["label"], "options": []})]
                if shown:
                    self.met.add(i)
                    continue
                about = f"{r['what']} {r['value']}"
                options = [c for c in clickable if c["role"] not in TOGGLES or words(c["label"]) & words(about)]
                options = [c for c in options if c["node"] not in self.rejected_fields.get(i, set())]
                options = [c for c in options if relevance(c, about)] or options
                picked = self.pick(f"set_{i}", options, state, f"Which element sets {r['what']} to {r['value']}?",
                                   about, allow_none=True)
                if not picked:
                    # Often the page is mid-render. Wait once; exhausted requirements stop the run.
                    return "WAIT", None, None, None, "wait", i, None
                # It may open a picker (a trip-type menu, a calendar); step 1 then chooses from what appears.
                return self.click(picked, "open", i)
            if "fill" in e["actions"]:
                return "TYPE_TEXT", e, e["actions"]["fill"], None, "fill", i, r["value"]
            if e["options"]:
                exact = [o for o in e["options"] if fold(option_label(o)) == fold(r["value"])]
                similar = [o for o in e["options"] if words(option_label(o)) & words(r["value"])]
                if exact or len(similar) == 1:
                    return "SELECT", e, (exact or similar)[0], None, "select", i, None
                answer = self.ask(state, {f"select_{i}": {
                    "type": "choice",
                    "instructions": f"Which option sets {r['what']} to {r['value']}?",
                    "criteria": {**{str(n): option_label(o) for n, o in enumerate(e["options"])},
                                 "none": "None of these options can set the requested value"},
                }})[f"select_{i}"]
                if answer["choice"] == "none":
                    return "BLOCKED", None, None, None, "blocked", i, None
                option = e["options"][int(answer["choice"])]
                return "SELECT", e, option, (e, answer), "select", i, None
            return "CLICK", e, e["actions"]["click"], None, "toggle" if e["role"] in TOGGLES else "open", i, None

        # Filled controls are not yet applied. Only submit controls may be offered here, even if a detail
        # button happens to have a higher Laya score. Keep native form associations when available.
        if self.typed:
            buttons = [e for e in clickable if e["role"] == "button"
                       and (e["is_submit"] or is_submit_label(e["label"]))
                       and (not self.dirty_forms or e["form"] is None or e["form"] in self.dirty_forms)]
            associated = [e for e in buttons if e["is_submit"] and e["form"] in self.dirty_forms]
            buttons = associated or buttons
            picked = self.pick("submit", buttons, f"Goal: {self.goal}",
                               "Which button applies the filled search/filter form?", "")
            if picked:
                return self.click(picked, "submit", None)
            implicit = [e for e in elements if "enter" in e["actions"] and e["form"] in self.dirty_forms]
            if len(implicit) == 1:
                e = implicit[0]
                return "PRESS_ENTER", e, e["actions"]["enter"], None, "submit", None, None
            return "BLOCKED", None, None, None, "blocked", None, None

        # 3. Everything stated is applied. Check the finish condition.
        finish = self.plan["finish"]
        navigated = self.edit_url is not None and location(page["url"]) != location(self.edit_url)
        if item:
            # An opened item names its page. Accept its title when it carries the item's words, or the words
            # of the element Laya chose to open it.
            # Laya may resolve a paraphrase or another language to an observed label. Confirm that
            # label dominates the destination title, not just a generic navigation word like "Flights".
            opened = self.item_step or (last if last and last["kind"] == "item" else None)
            chosen = (opened
                      and len(words(opened["label"]) & words(page["title"])) >= 0.6 * len(words(page["title"]))
                      and titled(page["title"], re.sub(r"^(?:view|read|open)\s+", "", opened["label"], flags=re.I)))
            headings = page.get("headings", [])
            identity = titled(page["title"], item) or bool(chosen)
            body = page.get("main_text", page["text"])
            needs_body = bool(re.search(r"\b(?:body|abstract|full text)\b|正文|摘要|全文",
                                        self.goal + " " + self.plan["finish"], re.I))
            readable = page.get("content_text", body) if needs_body else body
            heading_name = opened["label"] if chosen else item.rsplit("/", 1)[-1]
            def same_heading(h):
                title = fold(re.sub(r"^(?:title|标题)\s*[:：]\s*", "", h, flags=re.I))
                name = fold(re.sub(r"^(?:view|read|open)\s+", "", heading_name, flags=re.I))
                return title == name or title.startswith(name + " by ")

            heading_matches = any(same_heading(h) for h in headings) if headings else identity
            path = urlparse(page["url"]).path.rstrip("/").casefold()
            qualified = "/" in item and " " not in item
            qualified_match = qualified and path.endswith("/" + item.casefold())
            if qualified:
                identity = identity and qualified_match
                heading_matches = qualified_match
            search_route = "search" in path.split("/")
            result_listing = bool(re.search(r"displaying results \d|showing \d+.*results", page["text"], re.I))
            if (identity and heading_matches and matches_identity(body) and matches_authors(page.get("authors", []))
                    and len(readable.strip()) >= 80
                    and not (search_route or result_listing)):
                return "DONE", None, None, None, "done", None, None
            wrong_author = bool(authors and page.get("authors") and not matches_authors(page["authors"]))
            if opened and headings and (not heading_matches or wrong_author) and page["url"] != opened["url"]:
                back = next((a for a in page["actions"] if a["kind"] == "back"), None)
                if back and self.backtracks < 2:
                    self.rejected_urls.add(opened.get("href") or page["url"])
                    self.rejected_urls.add(page["url"])
                    return "BACK", None, back, None, "back", None, None
            if wrong_author and identity and not (search_route or result_listing):
                return "BLOCKED", None, None, None, "blocked", None, None
            if (identity and heading_matches and needs_body and len(readable.strip()) < 80
                    and not (search_route or result_listing) and self.waits >= MAX_RESULT_WAITS):
                self.terminal_reason = "No readable article body after the bounded loading wait."
                return "BLOCKED", None, None, None, "blocked", None, None
            if identity and not (search_route or result_listing):
                op = "WAIT" if self.waits < MAX_RESULT_WAITS else "BLOCKED"
                return op, None, None, None, op.lower(), None, None
        elif self.submitted or navigated or not reqs:
            # Laya's yes/no finish check was unreliable; contrasting page kinds separated real outcomes.
            state = f"Page title: {page['title']}\nPage: {summary(page['text'], finish)}"
            done = self.ask(state, {"done": {
                "type": "choice", "instructions": "Which best describes the current page?",
                "criteria": {"finish": finish, **UNFINISHED},
            }})["done"]
            # A submit that led to a new page while every stated value still holds is a search outcome:
            # accept it once Laya sees results, and wait while they load.
            # Modern search pages often update results in place without changing host or path. A recorded
            # submit plus still-satisfied requirements is enough to evaluate the visible result evidence.
            searched = self.acted == "submit" and self.submitted and self.met >= set(range(len(reqs)))
            # Count distinct requested values, not isolated tokens: a date's month/day/year alone
            # cannot be three pieces of result evidence. An open picker cannot be a completed search.
            value_words = {frozenset(words(r["value"])) for r in reqs if words(r["value"])}

            def result_evidence(text):
                tokens = words(text)
                matches = sum(len(tokens & value) >= min(2, len(value)) for value in value_words)
                return bool(value_words) and matches >= min(2, len(value_words))

            matching = [e for e in clickable if e["node"] not in self.before_submit_nodes
                        and not is_field(e) and result_evidence(e["label"])]
            matching_lines = [
                line for line in page["text"].splitlines()
                if line.strip() not in self.before_submit_lines and result_evidence(line)
            ]
            # A model verdict alone cannot prove that submission produced any results.
            if not any(e["dialog"] is not None for e in elements) and ((not reqs and done["choice"] == "finish") or (
                searched and max(len(matching), len(matching_lines)) >= 2
            )):
                return "DONE", None, None, None, "done", None, None
            if searched and self.waits < MAX_RESULT_WAITS:
                return "WAIT", None, None, None, "wait", None, None
            if searched:
                return "BLOCKED", None, None, None, "blocked", None, None
        if last and last["kind"] in {"submit", "item", "next"} and self.waits < 2 and (self.submitted or navigated):
            return "WAIT", None, None, None, "wait", None, None
        mapped = {self.fields.get(i) for i in range(len(reqs))}
        fresh = {e["node"] for e in clickable if last and e["node"] not in last["before"]}
        # A next-step target clicked twice already has shown it does not advance the goal.
        candidates = [e for e in clickable if e["node"] not in mapped and self.tried.get(e["label"], 0) < 2
                      and e.get("href", "") not in self.rejected_urls and not e.get("pagination_next")]

        state = f"Goal: {self.goal}\nFinish condition: {finish}\nPage title: {page['title']}\nPage: {page['text']}"
        if item:
            # Exact card titles bind descriptive text to otherwise opaque ID links.
            exact_cards = [e for e in candidates if fold(e.get("result_title", "")) == fold(item) or (
                "/" in item and fold(e.get("result_title", "")) == fold(item.rsplit("/", 1)[-1])
                and urlparse(e.get("href", "")).path.rstrip("/").casefold().endswith("/" + item.casefold())
            )]
            exact_cards = [e for e in exact_cards if matches_identity(e.get("result_context", ""))
                           and matches_authors(e.get("result_authors", []))]
            detail_cards = [e for e in exact_cards if not re.search(
                r"(?:\.pdf(?:$|[?#])|/pdf/)", e.get("href", ""), re.I)
                and fold(e["label"]) not in {"pdf", "html", "download"}]
            exact_cards = detail_cards or exact_cards
            primary = [e for e in exact_cards if e.get("result_primary")]
            exact_cards = primary or exact_cards
            named = exact_cards or [e for e in candidates if relevance(e, item) and not is_field(e)
                                    and not e.get("result_title")]
            named = [e for e in named if matches_identity(e.get("result_context", "") or e["label"])
                     and matches_authors(e.get("result_authors", []))]
            if not exact_cards and self.submitted and not self.refined_query:
                # Use phrase search only where the observed form explicitly offers title search.
                # No site-specific URL or selector is constructed.
                title_search = any(fold(option_label(o)) in {"title", "标题"}
                                   for e in elements for o in e["options"])
                queries = [e for e in elements if "fill" in e["actions"]
                           and is_search_label(e["label"]) and fold(e["current"]) == fold(item)]
                if title_search and len(queries) == 1 and not any(c in item for c in '\"\n'):
                    e = queries[0]
                    return "TYPE_TEXT", e, e["actions"]["fill"], None, "refine_query", None, f'"{item}"'
            if not exact_cards and self.submitted and page["url"] not in self.ranked_pages:
                ranks = [(e, o) for e in elements for o in e["options"]
                         if fold(option_label(o)) in {"relevance", "相关性", "相关度"}]
                if len(ranks) == 1:
                    e, option = ranks[0]
                    return "SELECT", e, option, None, "rank_results", None, None
            # Navigation acknowledges a submit, not completion of asynchronous results. An exact
            # title can be acted on immediately; otherwise give results time to arrive, then inspect
            # lower viewports using only the scroll action offered by this observation.
            phrase = fold(item)
            strong = exact_cards or [e for e in named if phrase and phrase in fold(e["label"])]
            if (self.submitted or any(e.get("result_title") for e in elements)) and not strong:
                if not any(e.get("result_title") for e in elements) and time.monotonic() < self.result_deadline:
                    return "WAIT", None, None, None, "wait", None, None
                scroll = next((a for a in page["actions"] if a["kind"] == "scroll" and a.get("delta", 0) > 0), None)
                position = (page["url"], page.get("scroll", {}).get("y", 0))
                if scroll and position not in self.result_scrolls and len(self.result_scrolls) < MAX_RESULT_SCROLLS:
                    return "SCROLL", None, scroll, None, "scroll", None, None
            if not strong and any(e.get("result_title") for e in elements):
                more_below = any(a["kind"] == "scroll" and a.get("delta", 0) > 0 for a in page["actions"])
                if not more_below and self.page_turns < 2:
                    next_links = {e["href"]: e for e in clickable if e.get("pagination_next") and e.get("href")
                                  and e["href"] != page["url"] and e["href"] not in self.pagination_seen
                                  and urlparse(e["href"]).netloc == urlparse(page["url"]).netloc}
                    if len(next_links) == 1:
                        e = next(iter(next_links.values()))
                        return "CLICK", e, e["actions"]["click"], None, "page_next", None, None
            named = strong or named
            # Near-duplicates ("completeness" vs "incompleteness") fooled Laya, so only the elements naming
            # the most of the item's words stay; Laya breaks exact ties.
            picked = self.pick("item", named, state, f"Which element opens {item}?", item, top_tier=True, margin=0)
            if not named and any(e.get("result_title") for e in candidates):
                # Structured results are available, but none matches: repair the search rather than
                # offering unrelated site navigation as an article candidate.
                return "BLOCKED", None, None, None, "blocked", None, None
            if not named and (identity_terms or authors):
                return "BLOCKED", None, None, None, "blocked", None, None
            if not named:
                # Lexical matching cannot bridge languages. Ask the multilingual choice model using
                # the original request; it may decline when no observed item matches.
                items = [e for e in candidates if not is_field(e) and not is_submit_label(e["label"])
                         and not e.get("result_title")]
                picked = self.pick("item", items, f"User request: {self.goal}",
                                   "Which visible item matches the item the user wants to open?", self.goal,
                                   allow_none=True)
            if picked:
                return self.click(picked, "item", None)
            return "BLOCKED", None, None, None, "blocked", None, None
        # Otherwise click what the goal names: the elements sharing the most words with it; Laya breaks ties.
        picked = self.pick("next", candidates, state, "Which element should be clicked next to reach the goal?",
                           f"{self.goal} {finish}", bonus=lambda e: e["node"] in fresh, top_tier=True, margin=0)
        if not picked:
            return "BLOCKED", None, None, None, "blocked", None, None
        return self.click(picked, "next", None)

    def click(self, picked, kind, req):
        e = picked[0]
        return "CLICK", e, e["actions"]["click"], picked, kind, req, None

    def assign(self, todo, free):
        """Match requirements to fields. Laya is asked both ways (which field sets this requirement, which
        requirement does this field hold); the product matched Flights fields better than either direction.
        The most confident pairs are assigned first, so two requirements never share one field.
        The reverse question can reject a pair: a field scoring higher for none stays unassigned.
        Laya shares one state per request, so each question is one call."""
        reqs = self.plan["requirements"]
        free = shortlist(free, " ".join(f"{reqs[i]['what']} {reqs[i]['value']}" for i in todo), 20)
        fields = {str(e["node"]): describe(e) for e in free}
        options = {str(i): f"{reqs[i]['what']} = {reqs[i]['value']}" for i in todo}
        by_requirement = {
            i: self.ask(f"Requirement: {options[str(i)]}", {f"field_{i}": {
                "type": "choice", "instructions": "Which element shows or sets this requirement?", "criteria": fields,
            }})[f"field_{i}"]["probabilities"]
            for i in todo
        }
        by_field = {
            node: self.ask(f"Form field: {text}", {f"holds_{node}": {
                "type": "choice", "instructions": "Which requirement does this form field hold?",
                "criteria": {**options, "none": "none of these"},
            }})[f"holds_{node}"]["probabilities"]
            for node, text in fields.items()
        }
        for i in todo:
            self.rejected_fields[i] = {int(node) for node in fields
                                       if by_field[node][str(i)] <= by_field[node]["none"]}
        # A shared word between the field's label and the requirement's name ("Departure", "departure date")
        # doubles the pair's score.
        label = {str(e["node"]): words(e["label"]) for e in free}
        pairs = sorted(
            (
                (by_requirement[i][node] * by_field[node][str(i)] * (1 + bool(label[node] & words(reqs[i]["what"]))),
                 i, node)
                for i in todo for node in fields
                if by_field[node][str(i)] > by_field[node]["none"]
            ),
            reverse=True,
        )
        done, used = set(), set()
        for _p, i, node in pairs:
            if i not in done and node not in used:
                self.fields[i] = int(node)
                done.add(i)
                used.add(node)

    def refresh(self, page, elements, by_node):
        """Map unmapped requirements to observed elements, then check their current values."""
        self.rejected_fields = {}
        reqs = self.plan["requirements"]
        open_reqs = [i for i in range(len(reqs)) if i not in self.frozen]
        fields = [e for e in elements if is_field(e)]
        # One field holds one requirement. Fields kept by other requirements are not offered again.
        taken = {self.fields.get(i) for i in open_reqs if self.fields.get(i) in by_node}
        todo = []
        for i in open_reqs:
            if i in self.met or self.fields.get(i) in by_node:
                continue
            r = reqs[i]
            free = [e for e in fields if e["node"] not in taken]
            # A dropdown offering exactly the requested value needs no model call.
            exact = [e for e in free if any(fold(option_label(o)) == fold(r["value"]) for o in e["options"])]
            # The planner names requirements by the observed field label when one sets them.
            named = [e for e in elements if plannable(e) and e["node"] not in taken
                     and fold(r["what"]) in {fold(e["label"]), fold(display(e))}]
            if not named:
                tokens = set(fold(r["what"]).split())
                named = [e for e in free if tokens and tokens <= set(fold(e["label"]).split())]
            # A unique field already displaying the exact non-numeric requested value is evidence
            # too (e.g. a seating-class control displaying Economy), without a speculative remap.
            current = [e for e in free if len(fold(r["value"])) >= 3 and not fold(r["value"]).isdigit()
                       and fold(e["current"]) == fold(r["value"])]
            match = exact if len(exact) == 1 else named if len(named) == 1 else current
            if len(match) == 1:
                self.fields[i] = match[0]["node"]
                taken.add(self.fields[i])
            else:
                todo.append(i)
        free = [e for e in fields if e["node"] not in taken]
        if todo and free:
            self.assign(todo, free)
        for i in todo:
            # A checkbox is named by what it sets; one sharing no words with the requirement cannot hold it.
            e = by_node.get(self.fields.get(i))
            r = reqs[i]
            if e and e["role"] in TOGGLES and not words(e["label"]) & words(f"{r['what']} {r['value']}"):
                del self.fields[i]
        checks = {}
        for i in open_reqs:
            e = by_node.get(self.fields.get(i))
            if e is None:
                continue
            verdict = settled(reqs[i], e)
            if verdict is None:
                if i not in self.met or self.satisfied_values.get(i) != (e["node"], e["current"]):
                    self.met.discard(i)
                    checks[i] = e
            elif verdict:
                self.met.add(i)
                self.satisfied_values[i] = (e["node"], e["current"])
            else:
                self.met.discard(i)
        for i, e in checks.items():
            r = reqs[i]
            answer = self.ask(f"Requirement: {r['what']} = {r['value']}\nCurrent value: {e['current']}", {
                f"met_{i}": {"type": "choice", "instructions": "Does the current value satisfy the requirement?",
                             "criteria": YES_NO},
            })[f"met_{i}"]
            if answer["choice"] == "yes":
                self.met.add(i)
                self.satisfied_values[i] = (e["node"], e["current"])
