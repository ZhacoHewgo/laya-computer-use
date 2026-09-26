"""Offline contracts for the local Laya policy. A fake stands in for the model; nothing is downloaded."""

import datetime
import time
from unittest.mock import Mock

import pytest

from laya_ultrafast import agent as loop
from laya_ultrafast import laya, model
from laya_ultrafast.browser import fingerprint


class FakeLaya:
    """Answers each choice question with `prefer(question_id, criteria)`, or the first option."""

    def __init__(self, prefer=None):
        self.prefer = prefer or (lambda _qid, criteria: next(iter(criteria)))
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        answers = {}
        for qid, q in questions.items():
            labels = list(q["criteria"])
            choice = self.prefer(qid, q["criteria"])
            rest = (1 - 0.7) / max(1, len(labels) - 1)
            probabilities = {label: 0.7 if label == choice else rest for label in labels}
            if len(labels) == 1:
                probabilities = {choice: 1.0}
            answers[qid] = {"choice": choice, "probabilities": probabilities, "confidence": 0.5}
        return {"answers": answers, "usage": {"input_tokens": 10}}


def page(actions, url="https://example.test/", title="Search", text="Search"):
    state = {"url": url, "title": title, "text": text, "scroll": {"y": 0}, "actions": actions}
    state["fingerprint"] = fingerprint(state)
    return state


FORM = [
    {"id": "e1", "kind": "fill", "label": "Destination", "role": "searchbox", "value": "", "node": 1},
    {"id": "e2", "kind": "click", "label": "Open Destination", "role": "searchbox", "value": "", "node": 1},
    {"id": "e3", "kind": "click", "label": "Find stays", "role": "button", "value": "", "node": 2},
    {"id": "e4", "kind": "click", "label": "View Casa Flora", "role": "button", "value": "", "node": 3},
    {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
]
PLAN = {"requirements": [{"what": "destination", "value": "Lisbon"}], "open": "Casa Flora", "finish": "It is open."}


@pytest.fixture
def fake(monkeypatch):
    f = FakeLaya()
    monkeypatch.setattr(laya, "laya", lambda: f)
    return f


def policy(plan=PLAN):
    p = laya.LayaPolicy("Find a stay in Lisbon and open Casa Flora.")
    p.plan, p.plan_meta = plan, {"model": "test", "latency_ms": 1}
    return p


def executed(history, decision, label=""):
    history.append({"choice": decision["choice"], "action": label})


def test_a_requirement_named_by_its_field_label_needs_no_mapping_call(fake):
    p = policy({"requirements": [{"what": "Destination", "value": "Lisbon"}], "open": None, "finish": "Seen."})
    d = p.choose(page(FORM), [])
    assert d["choice"] == "e1" and not any(k.startswith("field_") for _s, q in fake.calls for k in q)


def test_planner_gets_fields_without_result_or_submit_buttons(fake, monkeypatch):
    planner = Mock(return_value=(PLAN, {}))
    monkeypatch.setattr(laya, "plan_goal", planner)
    p = laya.LayaPolicy("Find a stay")
    p.choose(page(FORM), [])
    assert planner.call_args.args[1] == ["Destination"]


def test_clicking_generic_navigation_does_not_prove_item_opened(fake):
    p = policy({"requirements": [], "open": "flight options", "finish": "Flight options visible."})
    p.last = {"kind": "item", "label": "Flights", "req": None, "before": set()}
    d = p.choose(page(FORM, title="Find Cheap Flights Worldwide & Book Your Ticket"), [])
    assert d["operation"] != "DONE"


def test_goal_values_are_typed_without_a_per_field_text_call(fake):
    p = policy()
    d = p.choose(page(FORM), [])
    assert (d["operation"], d["choice"], d["text"]) == ("TYPE_TEXT", "e1", "Lisbon")
    assert d["target"] == "1"


def result_policy():
    p = policy({"requirements": [], "open": "Data Structures", "finish": "Chapter body open."})
    p.submitted = True
    return p


def test_async_results_wait_before_asking_model_for_missing_item(fake, monkeypatch):
    monkeypatch.setattr(laya.time, "monotonic", lambda: 100)
    p = result_policy()
    p.result_deadline = 105
    d = p.choose(page([], title="Search"), [])
    assert d["operation"] == "WAIT" and not fake.calls
    target = {"id": "chapter", "kind": "click", "node": 50, "role": "link", "label": "5. Data Structures"}
    d = p.choose(page([target]), [])
    assert d["choice"] == "chapter"  # No fixed five-second delay when the target has arrived.


def test_absent_result_stops_after_grace_without_resubmitting(fake, monkeypatch):
    monkeypatch.setattr(laya.time, "monotonic", lambda: 106)
    fake.prefer = lambda _qid, criteria: "none" if "none" in criteria else next(iter(criteria))
    p = result_policy()
    p.result_deadline = 105
    assert p.choose(page([]), [])["operation"] == "BLOCKED"


def test_search_results_scroll_until_target_becomes_visible(fake):
    p, history = result_policy(), []
    scroll = {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}
    first = p.choose(page([scroll]), history)
    assert first["choice"] == "scroll_down" and first["target_source"] == "rule"
    executed(history, first)
    target = {"id": "chapter", "kind": "click", "node": 50, "role": "link", "label": "5. Data Structures"}
    next_page = page([scroll, target])
    next_page["scroll"]["y"] = 560
    assert p.choose(next_page, history)["choice"] == "chapter"


def test_unmoved_scroll_is_not_repeated(fake):
    p, history = result_policy(), []
    snapshot = page([{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}])
    executed(history, p.choose(snapshot, history))
    assert p.choose(snapshot, history)["operation"] == "BLOCKED"


def test_submitted_search_field_scrolling_offscreen_does_not_restart_field_wait(fake):
    p = result_policy()
    p.plan["requirements"] = [{"what": "search", "value": "Data Structures"}]
    p.met = p.frozen = {0}
    p.search_added = True
    snapshot = page([{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}])
    assert p.choose(snapshot, [])["operation"] == "SCROLL"


def test_result_scroll_budget_is_bounded(fake):
    p, history = result_policy(), []
    snapshot = page([{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}])
    for i in range(laya.MAX_RESULT_SCROLLS):
        snapshot["scroll"]["y"] = i * 560
        d = p.choose(snapshot, history)
        assert d["operation"] == "SCROLL"
        executed(history, d)
    snapshot["scroll"]["y"] = laya.MAX_RESULT_SCROLLS * 560
    assert p.choose(snapshot, history)["operation"] == "BLOCKED"


def test_stem_overlap_is_not_an_exact_item_title(fake):
    p = result_policy()
    misleading = {"id": "wrong", "kind": "click", "node": 50, "role": "link",
                  "label": "struct — Interpret bytes as packed binary data"}
    scroll = {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}
    assert p.choose(page([misleading, scroll]), [])["operation"] == "SCROLL"
    exact = {"id": "right", "kind": "click", "node": 51, "role": "link", "label": "5. Data Structures"}
    assert p.choose(page([misleading, exact, scroll]), [])["choice"] == "right"


def test_typed_text_is_submitted_before_opening_a_result(fake):
    p, history = policy(), []
    executed(history, p.choose(page(FORM), history))
    filled = [dict(a, value="Lisbon") if a.get("node") == 1 else a for a in FORM]
    d = p.choose(page(filled), history)
    assert (d["operation"], d["choice"]) == ("CLICK", "e3")  # Find stays, not View Casa Flora


def test_wrong_detail_button_is_never_offered_as_submit(fake):
    fake.prefer = lambda _qid, criteria: next(reversed(criteria))
    p, history = policy(), []
    executed(history, p.choose(page(FORM), history))
    filled = [
        dict(a, value="Lisbon") if a.get("node") == 1 else dict(a, node=4) if a.get("node") == 3 else a
        for a in FORM
    ]
    d = p.choose(page(filled), history)
    assert d["choice"] == "e3"
    assert set(d["request"]["questions"]["submit"]["criteria"]) == {"2"}


def test_no_detail_open_until_submit_has_observed_effect(fake):
    p, history = policy(), []
    executed(history, p.choose(page(FORM), history))
    filled = [dict(a, value="Lisbon") if a.get("node") == 1 else a for a in FORM]
    executed(history, p.choose(page(filled), history))
    for _ in range(laya.MAX_RESULT_WAITS):
        d = p.choose(page(filled), history)
        assert d["operation"] == "WAIT"
        executed(history, d)
    assert p.choose(page(filled), history)["operation"] == "BLOCKED"


def test_planner_receives_visible_titles_for_cross_language_resolution(fake, monkeypatch):
    planner = Mock(return_value=(PLAN, {}))
    monkeypatch.setattr(laya, "plan_goal", planner)
    laya.LayaPolicy("打开关于置信度的文章").choose(page(FORM), [])
    assert "View Casa Flora" in planner.call_args.kwargs["items"]


@pytest.mark.parametrize("item", ["车次结果", "results", "搜索结果", "matching flight options"])
def test_generic_results_are_not_items_to_open(item):
    plan, meta = model.parse_plan({"requirements": [{"what": "city", "value": "Porto"}],
                                   "open": item, "finish": "Results visible."}, {})
    assert plan["open"] is None
    assert meta["plan_adjustments"]


def test_named_result_article_is_preserved():
    plan, _ = model.parse_plan({"requirements": [], "open": "Search results explained",
                                "finish": "Article open."}, {})
    assert plan["open"] == "Search results explained"


def test_planner_rejects_article_as_a_field_and_records_retry(monkeypatch):
    wrong = {"requirements": [{"what": "article title", "value": "Memory leaks"}],
             "open": "Memory leaks", "finish": "Article body visible."}
    right = {"requirements": [], "open": "Memory leaks", "finish": "Article body visible."}
    chat = Mock(side_effect=[(wrong, {}), (right, {})])
    monkeypatch.setattr(model, "chat_json", chat)
    plan, meta = model.plan_goal("Open Memory leaks", items=["Memory leaks"])
    assert plan == right and meta["model_calls"] == 2
    assert len(meta["responses"]) == 2
    assert "validation_error" in chat.call_args.args[1]


def test_observed_button_prefix_is_removed_from_item_title(monkeypatch):
    raw = {"requirements": [], "open": "View Mountain House", "finish": "Detail open."}
    monkeypatch.setattr(model, "chat_json", Mock(return_value=(raw, {})))
    plan, meta = model.plan_goal("Open Mountain House", items=["View Mountain House"])
    assert plan["open"] == "Mountain House"
    assert meta["responses"][0]["output"]["open"] == "View Mountain House"
    assert meta["plan_adjustments"][0]["from"] == "View Mountain House"


def test_submit_prefers_the_form_that_was_edited(fake):
    p, history = policy(), []
    form = [{**a, "form": 90} if a.get("node") in {1, 2} else a for a in FORM]
    form[2] = {**form[2], "is_submit": True}
    executed(history, p.choose(page(form), history))
    filled = [dict(a, value="Lisbon") if a.get("node") == 1 else a for a in form]
    filled.append({"id": "other", "kind": "click", "node": 99, "role": "button", "label": "Search site"})
    d = p.choose(page(filled), history)
    assert set(d["request"]["questions"]["submit"]["criteria"]) == {"2"}


def test_recorded_decision_plan_is_not_changed_by_later_search(fake):
    p = policy({"requirements": [], "open": "Memory leaks", "finish": "Article body visible."})
    actions = [{"id": "item", "kind": "click", "node": 50, "role": "link", "label": "Memory leaks"}]
    first = p.choose(page(actions), [])
    p.choose(page(FORM), [])  # Search requirement is added only to the working plan.
    assert first["request"]["plan"]["requirements"] == []
    assert p.plan["requirements"] == [{"what": "search", "value": "Memory leaks"}]


def test_initial_plan_excludes_search_added_on_the_first_decision(fake, monkeypatch):
    plan = {"requirements": [], "open": "Memory leaks", "finish": "Article open."}
    monkeypatch.setattr(laya, "plan_goal", Mock(return_value=(plan, {})))
    p = laya.LayaPolicy("Open Memory leaks")
    p.choose(page(FORM), [])
    assert p.initial_plan["requirements"] == []
    assert p.plan["requirements"] == [{"what": "search", "value": "Memory leaks"}]


def test_semantic_item_selection_uses_original_goal_and_observed_candidates(fake):
    p = policy({"requirements": [], "open": "规划器的不同表述", "finish": "Article body open."})
    p.goal = "打开解释置信度与正确性的文章"
    actions = [{"id": "item", "kind": "click", "node": 50, "role": "link",
                "label": "Confidence is not correctness"}]
    history = []
    d = p.choose(page(actions), history)
    assert d["choice"] == "item"
    assert p.goal in fake.calls[-1][0]
    assert "none" in d["request"]["questions"]["item"]["criteria"]
    executed(history, d)
    d = p.choose(page([], title="Confidence is not correctness · Site",
                      text="Article body with supporting evidence. " * 4), history)
    assert d["operation"] == "DONE"


@pytest.mark.parametrize("content", ['```json\n{"text":"Porto"}\n```', '{"text":"Porto"}\n```'])
def test_json_wrappers_are_removed_without_guessing_content(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "http://localhost:8771/v1")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    output, meta = model.chat_json("test", {})
    assert output == {"text": "Porto"} and meta["removed_json_fence"]


def test_multiple_json_objects_are_rejected(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "http://localhost:8771/v1")
    monkeypatch.setattr(model, "post_json", Mock(return_value={
        "choices": [{"message": {"content": '{"text":"Porto"} {"text":"Lisbon"}'}}],
    }))
    with pytest.raises(ValueError):
        model.chat_json("test", {})


def test_same_page_search_can_finish_from_visible_results(fake):
    p, history = policy({
        "requirements": [{"what": "Destination", "value": "Lisbon"}],
        "open": None,
        "finish": "Lisbon stays are visible.",
    }), []
    first = p.choose(page(FORM), history)
    executed(history, first)
    filled = [dict(a, value="Lisbon") if a.get("node") == 1 else a for a in FORM]
    submit = p.choose(page(filled), history)
    assert submit["choice"] == "e3"
    executed(history, submit)
    done = p.choose(page(filled, title="Lisbon stays", text="Lisbon stay Alpha\nLisbon stay Beta"), history)
    assert done["operation"] == "DONE"


def test_item_page_title_finishes_the_goal(fake):
    p = policy({"requirements": [], "open": "Casa Flora", "finish": "Casa Flora is open."})
    d = p.choose(page(FORM, title="Casa Flora · Forma", text="Accommodation details and rooms. " * 4), [])
    assert d["operation"] == "DONE" and d["choice"] == "DONE"


def test_rules_do_not_report_model_certainty(fake):
    d = policy().choose(page(FORM), [])
    assert d["confidence"] is None
    assert d["probabilities"][d["choice"]] is None
    assert d["operation_probabilities"] == {}
    assert d["usage"]["model_calls"] == len(fake.calls)


def test_exhausted_requirement_blocks_instead_of_succeeding(fake):
    p = policy()
    p.attempts[0] = 3
    assert p.choose(page(FORM), [])["operation"] == "BLOCKED"


def test_unchanged_text_after_submit_is_not_result_evidence(fake):
    p = policy({"requirements": [{"what": "Destination", "value": "Lisbon"}],
                "open": None, "finish": "Lisbon stays are visible."})
    history = []
    unchanged = "Lisbon stay Alpha\nLisbon stay Beta"
    executed(history, p.choose(page(FORM, text=unchanged), history))
    filled = [dict(a, value="Lisbon") if a.get("node") == 1 else a for a in FORM]
    executed(history, p.choose(page(filled, text=unchanged), history))
    assert p.choose(page(filled, text=unchanged), history)["operation"] == "WAIT"
    p.waits = laya.MAX_RESULT_WAITS
    assert p.choose(page(filled, text=unchanged), history)["operation"] == "BLOCKED"


@pytest.mark.parametrize("requirement", [{"what": "to", "value": None}, {"what": "", "value": "London"}, 3])
def test_malformed_requirements_cannot_be_silently_dropped(requirement):
    with pytest.raises(ValueError, match="no valid plan"):
        model.parse_plan({"requirements": [requirement], "finish": "Seen."}, {})


def test_a_search_results_title_is_not_the_item_page():
    assert laya.titled("Casa Flora · Forma", "Casa Flora")
    assert not laya.titled("Casa Flora - Search results - Forma", "Casa Flora")


def test_unicode_matching_preserves_chinese_and_folds_latin_accents():
    assert laya.fold("出发地 Zürich") == "出发地 zurich"
    assert laya.words("出发城市") & laya.words("出发地")
    assert laya.words("上海虹桥") & laya.words("上海")


def test_chinese_search_results_title_is_not_the_item_page():
    assert laya.titled("上海车票 · 本地演示", "上海车票")
    assert not laya.titled("上海车票 - 搜索结果", "上海车票")


def test_every_target_is_an_observed_action(fake):
    fake.prefer = lambda qid, criteria: list(criteria)[-1]
    p = policy({"requirements": [], "open": None, "finish": "Done."})
    d = p.choose(page(FORM), [])
    assert d["choice"] in {a["id"] for a in FORM} | {"DONE", "BLOCKED"}


def test_invalid_model_answer_executes_nothing(monkeypatch):
    broken = Mock(system_one=Mock(return_value={
        "answers": {"field_0": {"choice": "999", "probabilities": {"999": 1.0}, "confidence": 1.0}},
        "usage": {"input_tokens": 1},
    }))
    monkeypatch.setattr(laya, "laya", lambda: broken)
    form = [dict(a) for a in FORM] + [
        {"id": "e5", "kind": "fill", "label": "Guests", "role": "textbox", "value": "", "node": 4},
    ]
    plan = {"requirements": [{"what": "city", "value": "Lisbon"}], "open": None, "finish": "Seen."}
    with pytest.raises(ValueError, match="Invalid Laya"):
        policy(plan).choose(page(form), [])


def test_a_target_that_never_executes_is_dropped(fake):
    p = policy({"requirements": [], "open": "Casa Flora", "finish": "Casa Flora is open."})
    first = p.choose(page(FORM), [])
    assert first["choice"] == "e4"
    p.choose(page(FORM), [])  # The covered click raised StalePage; history did not grow.
    third = p.choose(page(FORM), [])
    assert third["choice"] != "e4"


@pytest.mark.parametrize(
    ("value", "current", "expected"),
    [
        ("October 20, 2026", "Tue, Oct 20", True),
        ("October 20, 2026", "Wed, Oct 21", False),
        ("Zurich", "Zürich", True),
        ("London", "", False),
        ("one-way", "Round trip", False),
    ],
)
def test_plain_code_settles_what_it_can(value, current, expected):
    element = {"role": "combobox", "current": current, "options": []}
    assert laya.settled({"what": "x", "value": value}, element) is expected


def test_plain_code_understands_chinese_dates():
    element = {"role": "textbox", "current": "2026年10月20日", "options": []}
    assert laya.settled({"what": "出发日期", "value": "2026年10月20日"}, element)
    element["current"] = "2026年10月21日"
    assert laya.settled({"what": "出发日期", "value": "2026年10月20日"}, element) is False


@pytest.mark.parametrize("label", ["搜索车次", "确认", "继续下一步", "查询"])
def test_chinese_submit_labels_are_detected(label):
    assert laya.is_submit_label(label)


def test_chinese_plan_types_exact_value_into_exact_field(fake):
    form = [
        {"id": "from", "kind": "fill", "label": "出发地", "role": "textbox", "value": "", "node": 11},
        {"id": "to", "kind": "fill", "label": "目的地", "role": "textbox", "value": "", "node": 12},
        {"id": "submit", "kind": "click", "label": "搜索车次", "role": "button", "value": "", "node": 13},
    ]
    p = policy({
        "requirements": [
            {"what": "出发地", "value": "杭州"},
            {"what": "目的地", "value": "上海"},
        ],
        "open": None,
        "finish": "页面显示杭州到上海的车次。",
    })
    decision = p.choose(page(form, title="车票搜索", text="出发地 目的地 搜索车次"), [])
    assert (decision["operation"], decision["choice"], decision["text"]) == ("TYPE_TEXT", "from", "杭州")


def test_checkbox_requirements_follow_the_checked_state():
    checked = {"role": "checkbox", "current": "checked", "options": []}
    assert laya.settled({"what": "free cancellation", "value": "checked"}, checked)
    assert not laya.settled({"what": "free cancellation", "value": "off"}, checked)


def test_plan_requires_a_finish_condition():
    plan, _ = model.parse_plan({"requirements": [{"what": "to", "value": "London"}], "finish": "Seen."}, {})
    assert plan == {"requirements": [{"what": "to", "value": "London"}], "open": None, "finish": "Seen."}
    with pytest.raises(ValueError, match="no valid plan"):
        model.parse_plan({"requirements": []}, {})


def test_local_text_model_needs_no_key_but_remote_does(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "http://localhost:11434/v1")
    assert model.field_text({"goal": "Fly from Zurich"})[0] == "Zurich"
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": "Fly from Zurich"})


def test_text_model_defaults_match_documented_openrouter(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("TEXT_MODEL", raising=False)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    assert model.field_text({"goal": "Fly from Zurich"})[0] == "Zurich"
    assert post.call_args.args[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert post.call_args.args[2]["model"] == "inception/mercury-2.5"

    from laya_ultrafast import demo

    assert demo.response_state()["text_model"] == "inception/mercury-2.5"


def test_agent_types_the_planned_value_without_the_text_helper(monkeypatch):
    helper = Mock()
    monkeypatch.setattr(loop, "field_text", helper)
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots, a.pending_text = False, None
    p = page(FORM)
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p, "goal": "g", "history": [], "decisions": [], "status": "predicted",
        "started_at": time.perf_counter(), "record": False, "text_calls": [],
        "decision": {"choice": "e1", "operation": "TYPE_TEXT", "target": "1", "text": "Lisbon", "confidence": 1.0,
                     "probabilities": {"e1": 1.0}, "latency_ms": 1, "usage": {}},
    }
    a.command("act", {"fingerprint": p["fingerprint"]})
    helper.assert_not_called()
    a.state["browser"].act.assert_called_once()
    assert a.state["browser"].act.call_args.kwargs["text"] == "Lisbon"


def test_flight_goal_uses_the_requested_date():
    from examples.flights import goal

    assert "October 20, 2026" in goal(datetime.date(2026, 10, 20))


def test_skyscanner_verification_reads_the_search_url():
    from examples.skyscanner import verify

    day = datetime.date(2026, 10, 20)
    good = {
        "url": "https://www.skyscanner.net/transport/flights/zrh/lond/261020/?adultsv2=1&cabinclass=economy&rtn=0",
        "text": "Best CHF 120 Cheapest CHF 98 Fastest",
    }
    assert verify(good, day)["passed"]
    assert not verify(dict(good, url=good["url"].replace("261020", "261021")), day)["passed"]
    assert not verify(dict(good, url="https://www.skyscanner.net/sttc/px/captcha-v2/index.html"), day)["passed"]


def test_reverse_field_rejection_prevents_forced_single_candidate(fake):
    fake.prefer = lambda qid, criteria: 'none' if qid.startswith('holds_') else next(iter(criteria))
    p = policy({'requirements': [{'what': 'passengers', 'value': '1 adult'}], 'open': None, 'finish': 'Results'})
    elements = laya.observed(page([{'id': 'trip', 'kind': 'click', 'node': 46, 'role': 'combobox',
                                   'label': 'Select your ticket type', 'value': 'One way'}]))
    p.answers, p.questions, p.tokens, p.model_calls = {}, {}, 0, 0
    p.assign([0], elements)
    assert p.fields == {}


def test_unchanged_semantic_value_stays_satisfied_but_changed_value_is_rechecked(fake):
    fake.prefer = lambda qid, criteria: 'yes' if qid.startswith('met_') else next(iter(criteria))
    p = policy({'requirements': [{'what': 'passengers', 'value': '1 adult'}], 'open': None, 'finish': 'Results'})
    p.fields[0] = 9
    actions = [{'id': 'people', 'kind': 'click', 'node': 9, 'role': 'button', 'label': '1 passenger'}]
    def refresh():
        snapshot = page(actions)
        elements = laya.observed(snapshot)
        p.answers, p.questions, p.tokens, p.model_calls = {}, {}, 0, 0
        p.refresh(snapshot, elements, {e['node']: e for e in elements})
    refresh()
    assert p.met == {0}
    calls = len(fake.calls)
    refresh()
    assert p.met == {0} and len(fake.calls) == calls
    actions[0]['label'] = '2 passengers'
    fake.prefer = lambda qid, criteria: 'no' if qid.startswith('met_') else next(iter(criteria))
    refresh()
    assert p.met == set() and len(fake.calls) > calls


def test_picker_confirmation_precedes_unrelated_requirements_and_is_not_form_submit(fake):
    p = policy({'requirements': [{'what': 'date', 'value': 'October 20, 2026'},
                                 {'what': 'passengers', 'value': '1 adult'}], 'open': None, 'finish': 'Results'})
    p.last = {'kind': 'pick', 'req': 0, 'dialog': 80, 'label': 'Tuesday, October 20, 2026'}
    p.typed = True
    snapshot = page([
        {'id': 'done', 'kind': 'click', 'node': 81, 'dialog': 80, 'role': 'button',
         'label': 'Done. Apply the selected date'},
        {'id': 'trip', 'kind': 'click', 'node': 82, 'dialog': 80, 'role': 'combobox',
         'label': 'Select your ticket type', 'value': 'One way'},
    ])
    history = []
    decision = p.choose(snapshot, history)
    assert decision['choice'] == 'done' and not fake.calls
    assert p.pending['kind'] == 'confirm'
    executed(history, decision)
    assert p.choose(snapshot, history)['operation'] == 'WAIT'
    assert not p.awaiting_submit and not p.submitted
    assert p.typed  # Still needs to submit the outer search form.


def test_rejected_field_is_not_reintroduced_by_fallback_click(fake):
    fake.prefer = lambda qid, criteria: 'none' if qid.startswith('holds_') else next(iter(criteria))
    p = policy({'requirements': [{'what': 'passengers', 'value': '1 adult'}], 'open': None, 'finish': 'Results'})
    snapshot = page([{'id': 'trip', 'kind': 'click', 'node': 46, 'role': 'combobox',
                      'label': 'Select your ticket type', 'value': 'One way'}])
    assert p.choose(snapshot, [])['operation'] == 'WAIT'
    assert p.fields == {}


def test_delayed_autocomplete_survives_wait_without_retyping_or_advancing(fake):
    p = policy({'requirements': [{'what': 'Origin', 'value': 'Zurich'},
                                 {'what': 'Destination', 'value': 'London'}], 'open': None, 'finish': 'Results'})
    origin = {'id': 'origin', 'kind': 'fill', 'node': 1, 'role': 'combobox', 'label': 'Origin', 'value': ''}
    destination = {'id': 'dest', 'kind': 'fill', 'node': 2, 'role': 'textbox', 'label': 'Destination', 'value': ''}
    history = []
    executed(history, p.choose(page([origin, destination]), history))
    origin.update(value='Zurich', expanded='true')
    decision = p.choose(page([origin, destination]), history)
    assert decision['operation'] == 'WAIT'
    executed(history, decision)
    option = {'id': 'suggestion', 'kind': 'click', 'node': 3, 'role': 'option', 'label': 'Zürich, Switzerland'}
    decision = p.choose(page([origin, destination, option]), history)
    assert decision['choice'] == 'suggestion'
    assert p.attempts[0] == 1


def test_exact_field_names_take_priority_over_partial_words(fake):
    p = policy({'requirements': [{'what': 'Where from?', 'value': 'Zurich'},
                                 {'what': 'Where to?', 'value': 'London'}], 'open': None, 'finish': 'Results'})
    snapshot = page([
        {'id': 'from', 'kind': 'fill', 'node': 1, 'role': 'textbox', 'label': 'Where from?', 'value': ''},
        {'id': 'to', 'kind': 'fill', 'node': 2, 'role': 'textbox', 'label': 'Where to?', 'value': ''},
    ])
    assert p.choose(snapshot, [])['choice'] == 'from'
    assert p.fields == {0: 1, 1: 2} and not fake.calls


@pytest.mark.parametrize('required,current,expected', [
    ('one-way', 'Round trip', False), ('单程', '往返', False),
    ('round-trip', 'One way', False), ('one-way', 'One way', True),
])
def test_mutually_exclusive_trip_values_are_checked_without_model(required, current, expected):
    element = {'role': 'combobox', 'current': current, 'options': []}
    assert laya.settled({'what': 'Change ticket type. Round trip', 'value': required}, element) is expected


def test_wrong_trip_is_not_satisfied_even_when_model_always_says_yes(fake):
    fake.prefer = lambda qid, criteria: 'yes' if qid.startswith('met_') else next(iter(criteria))
    p = policy({'requirements': [{'what': 'Change ticket type. Round trip', 'value': 'one-way'}],
                'open': None, 'finish': 'Flights visible'})
    d = p.choose(page([{'id': 'trip', 'kind': 'click', 'node': 1, 'role': 'combobox',
                       'label': 'Change ticket type. Round trip', 'value': 'Round trip'}]), [])
    assert d['choice'] == 'trip' and not p.met
    assert not any(qid.startswith('met_') for _state, questions in fake.calls for qid in questions)


@pytest.mark.parametrize('dialog', [None, 91])
def test_calendar_date_tokens_do_not_prove_search_results(fake, dialog):
    p = policy({'requirements': [{'what': 'origin', 'value': 'Zurich'},
                                 {'what': 'destination', 'value': 'London'},
                                 {'what': 'date', 'value': 'October 20, 2026'}],
                'open': None, 'finish': 'Flight options visible'})
    p.met = p.frozen = {0, 1, 2}
    p.submitted, p.acted = True, 'submit'
    actions = [{'id': str(i), 'kind': 'click', 'node': i, 'role': 'button', 'dialog': dialog,
                'label': 'Tuesday, October 20, 2026'} for i in (4, 5)]
    d = p.choose(page(actions, text='October 20, 2026\nTuesday, October 20, 2026'), [])
    assert d['operation'] == 'WAIT'
    p.waits = laya.MAX_RESULT_WAITS
    assert p.choose(page(actions), [])['operation'] == 'BLOCKED'


def test_open_dialog_blocks_done_even_with_matching_result_like_text(fake):
    p = policy({'requirements': [{'what': 'origin', 'value': 'Zurich'},
                                 {'what': 'destination', 'value': 'London'}],
                'open': None, 'finish': 'Flight options visible'})
    p.met = p.frozen = {0, 1}
    p.submitted, p.acted = True, 'submit'
    actions = [{'id': 'done', 'kind': 'click', 'node': 4, 'role': 'button', 'dialog': 91, 'label': 'Done'}]
    assert p.choose(page(actions, text='Zurich London option A\nZurich London option B'), [])['operation'] == 'WAIT'


def test_organization_title_is_not_repository_identity():
    assert not laya.titled("Browser Use · GitHub", "browser-use/jev-ultrafast")
    assert not laya.titled("Completeness", "Incompleteness")


def test_title_without_body_does_not_finish(fake):
    p = policy({"requirements": [], "open": "Casa Flora", "finish": "Details are visible."})
    assert p.choose(page([], title="Casa Flora", text="Loading"), [])["operation"] != "DONE"


def test_existing_search_requirement_is_not_duplicated(fake):
    p = policy({"requirements": [{"what": "Search books", "value": "Pride and Prejudice by Jane Austen"}],
                "open": "Pride and Prejudice", "finish": "Book details open."})
    actions = [{"id": "q", "kind": "fill", "node": 1, "role": "searchbox", "label": "Search books"}]
    assert p.choose(page(actions), [])["operation"] == "TYPE_TEXT"
    assert len(p.plan["requirements"]) == 1


def test_planner_sees_options_and_search_navigation(fake, monkeypatch):
    planner = Mock(return_value=({"requirements": [], "open": "Paper", "finish": "Paper visible."}, {}))
    monkeypatch.setattr(laya, "plan_goal", planner)
    actions = [{"id": "s", "kind": "click", "node": 1, "role": "link", "label": "Search"},
               {"id": "o", "kind": "select", "node": 2, "role": "combobox", "label": "Category → Physics",
                "value": "physics"}]
    laya.LayaPolicy("Find Paper").choose(page(actions), [])
    assert "Search" in planner.call_args.kwargs["items"]
    assert planner.call_args.kwargs["controls"][0]["options"]


def test_duplicate_planner_fields_are_rejected(monkeypatch):
    output = {"requirements": [{"what": "Search", "value": "A"}, {"what": "Search", "value": "B"}],
              "open": "A", "finish": "Details."}
    monkeypatch.setattr(model, "chat_json", Mock(return_value=(output, {})))
    with pytest.raises(ValueError, match="only once"):
        model.plan_goal("Find A", ["Search"], attempts=1)


def test_repair_is_bounded_and_receives_failure_history(fake, monkeypatch):
    plan = {"requirements": [], "open": "Missing title", "finish": "Body visible."}
    planner = Mock(side_effect=lambda *a, **k: (dict(plan), {}))
    monkeypatch.setattr(laya, "plan_goal", planner)
    p = laya.LayaPolicy("Find Missing title")
    history = []
    for _ in range(8):
        d = p.choose(page([]), history)
        if d["operation"] == "BLOCKED":
            break
        executed(history, d)
    assert d["operation"] == "BLOCKED"
    assert planner.call_count == 3 and p.repairs == 2
    assert planner.call_args.kwargs["feedback"]["history"]
    assert len(p.planning_events) == 3


def test_search_results_named_after_book_do_not_finish(fake):
    p = policy({"requirements": [], "open": "Pride and Prejudice", "finish": "Book details."})
    d = p.choose(page([], url="https://example.test/ebooks/search/?query=pride",
                      title="Books: Pride and Prejudice", text="Displaying results 1–11\n" + "Book listing " * 20), [])
    assert d["operation"] != "DONE"


def test_navigation_disambiguates_search_link_from_submit_button(fake):
    p = policy({"requirements": [], "open": "Paper", "finish": "Paper body.", "navigate": "Search"})
    actions = [{"id": "nav", "kind": "click", "node": 1, "role": "link", "label": "Search"},
               {"id": "submit", "kind": "click", "node": 2, "role": "button", "label": "Search", "is_submit": True}]
    assert p.choose(page(actions), [])["choice"] == "nav"


def test_dropdown_can_decline_an_unavailable_value(fake):
    fake.prefer = lambda qid, criteria: "none" if qid.startswith("select_") else next(iter(criteria))
    p = policy({"requirements": [{"what": "Category", "value": "Attention Is All You Need"}],
                "open": "Attention Is All You Need", "finish": "Paper body."})
    actions = [{"id": "a", "kind": "select", "node": 1, "role": "combobox",
                "label": "Category → Physics", "value": "physics"},
               {"id": "b", "kind": "select", "node": 1, "role": "combobox",
                "label": "Category → Mathematics", "value": "mathematics"}]
    assert p.choose(page(actions), [])["operation"] == "BLOCKED"


def test_planner_cannot_invent_navigation_target(monkeypatch):
    output = {"requirements": [], "open": "Paper", "finish": "Paper body.", "navigate": "Invented link"}
    monkeypatch.setattr(model, "chat_json", Mock(return_value=(output, {})))
    with pytest.raises(ValueError, match="observed"):
        model.plan_goal("Find Paper", items=["Search"], attempts=1)


def test_qualified_target_does_not_finish_on_child_page(fake):
    p = policy({"requirements": [], "open": "org/project", "finish": "Project page."})
    d = p.choose(page([], url="https://example.test/org/project/commit/123",
                      title="org/project", text="A commit description and changed files. " * 5), [])
    assert d["operation"] != "DONE"


def test_observed_implicit_submit_is_used_when_button_absent(fake):
    p = policy({"requirements": [], "open": "Paper", "finish": "Paper body."})
    p.typed = True
    p.dirty_forms = {12}
    actions = [{"id": "q", "kind": "fill", "node": 1, "role": "searchbox", "label": "Search", "form": 12},
               {"id": "enter", "kind": "enter", "node": 1, "role": "searchbox", "label": "Submit Search", "form": 12}]
    p.search_added = True
    assert p.choose(page(actions), [])["operation"] == "PRESS_ENTER"


def test_item_navigation_preserves_completed_in_place_search(fake):
    p = policy({"requirements": [{"what": "Search repositories", "value": "org/project"}],
                "open": "org/project", "finish": "Repository body."})
    p.submitted = True
    p.submit_url = "https://example.test/org/repositories"
    p.met = {0}
    d = p.choose(page([], url="https://example.test/org/project", title="org/project",
                      text="The project README, installation and usage. " * 5), [])
    assert p.frozen == {0}
    assert d["operation"] == "DONE"


def test_paper_title_contained_in_different_paper_does_not_finish(fake):
    p = policy({"requirements": [], "open": "Attention Is All You Need", "finish": "Paper body."})
    current = page([], title="FAIR: Focused Attention Is All You Need for Generative Recommendation",
                   text="A different paper with a different author and abstract. " * 5)
    current["headings"] = [current["title"]]
    assert p.choose(current, [])["operation"] != "DONE"


def test_exact_result_card_beats_derivative_title(fake):
    p = policy({"requirements": [], "open": "Attention Is All You Need", "finish": "Paper body."})
    p.submitted = True
    actions = [
        {"id": "wrong", "kind": "click", "node": 1, "role": "link", "label": "paper:9999",
         "result_title": "Focused Attention Is All You Need for Recommendation", "href": "https://test/wrong"},
        {"id": "right", "kind": "click", "node": 2, "role": "link", "label": "paper:1234",
         "result_title": "Attention Is All You Need", "result_context": "Authors: Example Author",
         "href": "https://test/right"},
        {"id": "pdf", "kind": "click", "node": 3, "role": "link", "label": "PDF",
         "result_title": "Attention Is All You Need", "href": "https://test/right.pdf"},
    ]
    d = p.choose(page(actions), [])
    assert d["choice"] == "right"
    assert "Example Author" in str(d["request"]["questions"])


def test_derivative_card_is_not_used_as_semantic_fallback(fake):
    p = policy({"requirements": [], "open": "Attention Is All You Need", "finish": "Paper body."})
    actions = [{"id": "wrong", "kind": "click", "node": 1, "role": "link", "label": "Attention Is All You Need again",
                "result_title": "Attention Is All You Need again", "href": "https://test/wrong"}]
    assert p.choose(page(actions), [])["operation"] == "BLOCKED"


def test_wrong_item_can_return_only_with_observed_back_action(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.item_step = {"kind": "item", "label": "paper:12", "url": "https://test/search", "href": "https://test/wrong"}
    current = page([{"id": "back", "kind": "back", "label": "Back"}],
                   url="https://test/wrong", title="Another paper", text="Different paper body. " * 10)
    current["headings"] = ["Another paper"]
    assert p.choose(current, [])["operation"] == "BACK"
    assert "https://test/wrong" in p.rejected_urls
    p.backtracks = 2
    assert p.choose(current, [])["operation"] != "BACK"


def test_relevance_sort_uses_only_observed_option(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.submitted = True
    actions = [{"id": "rank", "kind": "select", "node": 1, "role": "combobox",
                "label": "Sort → Relevance", "value": "relevance", "current_value": "Newest"}]
    assert p.choose(page(actions), [])["choice"] == "rank"
    p.ranked_pages.add("https://example.test/")
    assert p.choose(page(actions), [])["operation"] != "SELECT"


def test_changing_sort_requires_applying_its_form(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.submitted = True
    actions = [{"id": "rank", "kind": "select", "node": 1, "role": "combobox", "form": 7,
                "label": "Sort → Relevance", "value": "relevance", "current_value": "Newest"},
               {"id": "go", "kind": "click", "node": 2, "role": "button", "form": 7,
                "label": "Go", "is_submit": True}]
    history = []
    d = p.choose(page(actions), history)
    assert d["choice"] == "rank"
    executed(history, d)
    assert p.choose(page(actions[1:]), history)["choice"] == "go"


def test_back_waits_for_navigation_before_another_mutation(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.item_step = {"kind": "item", "label": "paper:12", "url": "https://test/search", "href": "https://test/wrong"}
    current = page([{"id": "back", "kind": "back", "label": "Back"}],
                   url="https://test/wrong", title="Another paper", text="Different paper body. " * 10)
    current["headings"] = ["Another paper"]
    history = []
    d = p.choose(current, history)
    executed(history, d)
    assert p.choose(current, history)["operation"] == "WAIT"
    assert p.backtracks == 1


def test_qualified_result_title_is_bound_to_its_observed_path(fake):
    p = policy({"requirements": [], "open": "org/project", "finish": "Project body."})
    actions = [{"id": "wrong", "kind": "click", "node": 1, "role": "link", "label": "project",
                "result_title": "project", "href": "https://test/other/project"},
               {"id": "right", "kind": "click", "node": 2, "role": "link", "label": "project",
                "result_title": "project", "href": "https://test/org/project"}]
    assert p.choose(page(actions), [])["choice"] == "right"


def test_query_only_navigation_acknowledges_submit(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.awaiting_submit = True
    p.submit_url = "https://test/search?order=newest"
    p.before_submit_lines = {"Search"}
    p.choose(page([], url="https://test/search?order=relevance"), [])
    assert not p.awaiting_submit
    assert p.submitted


def test_phrase_refinement_requires_observed_title_search_and_is_bounded(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.submitted = True
    actions = [{"id": "q", "kind": "fill", "node": 1, "role": "searchbox", "label": "Search terms",
                "value": "Desired paper", "form": 8},
               {"id": "scope", "kind": "select", "node": 2, "role": "combobox", "label": "Field → Title",
                "value": "title", "current_value": "All fields", "form": 8}]
    p.search_added = True
    d = p.choose(page(actions), [])
    assert d["operation"] == "TYPE_TEXT" and d["text"] == '"Desired paper"'
    p.refined_query = True
    assert p.choose(page(actions), [])["operation"] != "TYPE_TEXT"


def test_unrelated_navigation_is_not_fallback_for_nonmatching_result_cards(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    actions = [{"id": "wrong", "kind": "click", "node": 1, "role": "link", "label": "paper:1",
                "result_title": "Different paper", "href": "https://test/wrong"},
               {"id": "privacy", "kind": "click", "node": 2, "role": "link", "label": "Privacy"}]
    assert p.choose(page(actions), [])["operation"] == "BLOCKED"


def test_visible_result_cards_allow_scanning_without_loading_delay(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.submitted = True
    p.result_deadline = time.monotonic() + 100
    actions = [{"id": "wrong", "kind": "click", "node": 1, "role": "link", "label": "paper:1",
                "result_title": "Different paper", "href": "https://test/wrong"},
               {"id": "down", "kind": "scroll", "label": "Scroll down", "delta": 560}]
    assert p.choose(page(actions), [])["operation"] == "SCROLL"


def test_primary_result_link_is_preferred_to_author_link(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    actions = [{"id": "author", "kind": "click", "node": 1, "role": "link", "label": "Example Author",
                "result_title": "Desired paper", "href": "https://test/author"},
               {"id": "paper", "kind": "click", "node": 2, "role": "link", "label": "paper:12",
                "result_title": "Desired paper", "href": "https://test/paper", "result_primary": True}]
    assert p.choose(page(actions), [])["choice"] == "paper"


def paginated_page(url="https://test/search?page=1", target="https://test/search?page=2"):
    return page([
        {"id": "other", "kind": "click", "node": 1, "role": "link", "label": "Other article",
         "result_title": "Other article", "href": "https://test/other"},
        {"id": "next", "kind": "click", "node": 2, "role": "link", "label": "Next page",
         "href": target, "pagination_next": True},
    ], url=url)


def test_paginate_only_after_visible_results_end(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    current = paginated_page()
    current["actions"].append({"id": "down", "kind": "scroll", "label": "Scroll down", "delta": 560})
    assert p.choose(current, [])["operation"] == "SCROLL"
    current["actions"].pop()
    assert p.choose(current, [])["choice"] == "next"


def test_pagination_waits_for_results_and_does_not_replay_click(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    history, current = [], paginated_page()
    executed(history, p.choose(current, history))
    assert p.choose(current, history)["operation"] == "WAIT"
    assert p.page_turns == 1
    changed = paginated_page(url="https://test/search?page=2", target="https://test/search?page=3")
    changed["actions"][0]["result_title"] = "Another article"
    assert p.choose(changed, history)["choice"] == "next"
    assert p.awaiting_page is None


def test_pagination_cycle_and_budget_are_rejected(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    p.pagination_seen = {"https://test/search?page=1"}
    assert p.choose(paginated_page(url="https://test/search?page=2", target="https://test/search?page=1"), [
    ])["operation"] == "BLOCKED"
    p.pagination_seen.clear()
    p.page_turns = 2
    assert p.choose(paginated_page(), [])["operation"] == "BLOCKED"


def test_plain_next_button_is_not_pagination(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    current = paginated_page()
    current["actions"][1]["pagination_next"] = False
    current["actions"][1]["role"] = "button"
    assert p.choose(current, [])["operation"] == "BLOCKED"


def test_pagination_accepts_new_cards_without_url_change(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    history, current = [], paginated_page()
    executed(history, p.choose(current, history))
    current["actions"] = [{"id": "target", "kind": "click", "node": 3, "role": "link",
                           "label": "Desired paper", "result_title": "Desired paper", "href": "https://test/desired"}]
    assert p.choose(current, history)["choice"] == "target"
    assert p.awaiting_page is None


def test_pagination_detects_same_results_at_a_different_url(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    current, history = paginated_page(), []
    executed(history, p.choose(current, history))
    alias = paginated_page(url="https://test/search?page=1&alias=yes")
    assert p.choose(alias, history)["operation"] == "BLOCKED"


def test_pagination_waits_for_empty_loading_results(fake):
    p = policy({"requirements": [], "open": "Desired paper", "finish": "Paper body."})
    current, history = paginated_page(), []
    executed(history, p.choose(current, history))
    assert p.choose(page([], url="https://test/search?page=2", text="Loading"), history)["operation"] == "WAIT"


def test_search_suggestion_navigation_does_not_refill_destination_search(fake):
    plan = {"requirements": [{"what": "Search", "value": "Desired article"}],
            "open": "Desired article", "finish": "Article body."}
    p = policy(plan)
    p.last = {"kind": "pick", "role": "link", "req": 0, "url": "https://test/", "label": "Desired article"}
    p.typed = True
    current = page([{"id": "search", "kind": "fill", "node": 5, "role": "searchbox", "label": "Search"}],
                   url="https://test/article", title="Desired article", text="The article's detailed body. " * 6)
    current["headings"] = ["Desired article"]
    assert p.choose(current, [])["operation"] == "DONE"
    assert p.frozen == {0} and not p.typed


def test_nonmatching_suggestion_navigation_does_not_satisfy_goal(fake):
    p = policy({"requirements": [{"what": "Search", "value": "Desired article"}],
                "open": "Desired article", "finish": "Article body."})
    p.last = {"kind": "pick", "role": "link", "req": 0, "url": "https://test/", "label": "Something else"}
    assert p.choose(page([], url="https://test/other", title="Different article"), [])["operation"] != "DONE"
    assert not p.frozen


def test_identity_terms_filter_same_title_cards(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'identity_terms': ['Ada Example'], 'finish': 'Open.'})
    cards = [{'id': 'wrong', 'kind': 'click', 'node': 1, 'role': 'link', 'label': 'Open article',
              'result_title': 'Shared Title', 'result_context': 'Shared Title by Other Author',
              'href': 'https://test/wrong'},
             {'id': 'right', 'kind': 'click', 'node': 2, 'role': 'link', 'label': 'Open article',
              'result_title': 'Shared Title', 'result_context': 'Shared Title by Ada Example',
              'href': 'https://test/right'}]
    assert p.choose(page(cards), [])['choice'] == 'right'
    p = policy(p.plan)
    assert p.choose(page(cards[:1]), [])['operation'] == 'BLOCKED'


def test_same_title_detail_cannot_complete_without_identity_terms(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'identity_terms': ['Ada Example'], 'finish': 'Open.'})
    current = page([], title='Shared Title', text='Other Author. ' + 'Long article body. ' * 10)
    current['headings'] = ['Shared Title']
    assert p.choose(current, [])['operation'] != 'DONE'
    current['text'] += ' Ada Exampleton'
    assert p.choose(current, [])['operation'] != 'DONE'
    current['text'] += ' Ada Example'
    assert p.choose(current, [])['operation'] == 'DONE'


@pytest.mark.parametrize('terms', ['Ada', [None], [''], ['x' * 161], ['x'] * 9])
def test_identity_terms_require_bounded_literal_phrases(terms):
    with pytest.raises(ValueError):
        model.parse_plan({'requirements': [], 'open': 'Shared Title', 'finish': 'Open.', 'identity_terms': terms}, {})


def test_planner_preserves_identity_terms():
    plan, _ = model.parse_plan({'requirements': [], 'open': 'Shared Title', 'finish': 'Open.',
                                'identity_terms': ['Ada Example', 'Ada Example']}, {})
    assert plan['identity_terms'] == ['Ada Example']


def test_repair_does_not_drop_initial_identity_terms(fake, monkeypatch):
    p = policy({'requirements': [], 'open': 'Shared Title', 'identity_terms': ['Ada Example'], 'finish': 'Open.'})
    p.initial_plan = dict(p.plan)
    monkeypatch.setattr(laya, 'plan_goal', lambda *_a, **_k: (
        {'requirements': [], 'open': 'Shared Title', 'finish': 'Open.'}, {}))
    p.make_plan(page([]), [], reason='No matching card')
    assert p.plan['identity_terms'] == ['Ada Example']


def test_author_mention_is_not_authorship(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'authors': ['Ada Example'], 'finish': 'Open.'})
    current = page([], title='Shared Title', text='Mentions Ada Example. ' + 'Article body. ' * 12)
    current['headings'] = ['Shared Title']
    current['authors'] = [{'value': 'Other Author', 'source': 'author label'}]
    assert p.choose(current, [])['operation'] != 'DONE'
    current['authors'] = [{'value': 'Ada Example', 'source': 'author label'}]
    assert p.choose(current, [])['operation'] == 'DONE'


def test_card_author_evidence_overrides_body_mentions(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'authors': ['Ada Example'], 'finish': 'Open.'})
    card = {'id': 'wrong', 'kind': 'click', 'node': 1, 'role': 'link', 'label': 'Open article',
            'result_title': 'Shared Title', 'result_context': 'Mentions Ada Example.',
            'result_authors': [{'value': 'Other Author', 'source': 'author label'}], 'href': 'https://test/wrong'}
    assert p.choose(page([card]), [])['operation'] == 'BLOCKED'
    card['result_authors'] = [{'value': 'Ada Example', 'source': 'author label'}]
    assert p.choose(page([card]), [])['choice'] == 'wrong'


def test_repair_retains_authors(fake, monkeypatch):
    p = policy({'requirements': [], 'open': 'Shared Title', 'authors': ['Ada Example'], 'finish': 'Open.'})
    p.initial_plan = dict(p.plan)
    monkeypatch.setattr(laya, 'plan_goal', lambda *_a, **_k: (
        {'requirements': [], 'open': 'Shared Title', 'finish': 'Open.'}, {}))
    p.make_plan(page([]), [], reason='No matching author')
    assert p.plan['authors'] == ['Ada Example']


@pytest.mark.parametrize('authors', ['Ada', [None], [''], ['...'], ['x' * 161], ['x'] * 9])
def test_author_plan_rejects_invalid_values(authors):
    with pytest.raises(ValueError):
        model.parse_plan({'requirements': [], 'open': 'Shared Title', 'finish': 'Open.', 'authors': authors}, {})


def test_author_plan_preserves_names():
    plan, _ = model.parse_plan({'requirements': [], 'open': 'Shared Title', 'finish': 'Open.',
                               'authors': ['Ada Example']}, {})
    assert plan['authors'] == ['Ada Example']


def test_conflicting_author_returns_to_results_without_reopening(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'authors': ['Ada Example'], 'finish': 'Open.'})
    p.item_step = {'kind': 'item', 'label': 'Shared Title', 'url': 'https://test/search', 'href': 'https://test/wrong'}
    current = page([{'id': 'back', 'kind': 'back', 'label': 'Back'}],
                   url='https://test/wrong', title='Shared Title', text='Mentions Ada Example. ' * 10)
    current['headings'] = ['Shared Title']
    current['authors'] = [{'value': 'Other Author', 'source': 'author label'}]
    assert p.choose(current, [])['operation'] == 'BACK'
    assert 'https://test/wrong' in p.rejected_urls
    p.backtracks = 2
    assert p.choose(current, [])['operation'] == 'BLOCKED'


@pytest.mark.parametrize('requested,value,individual,expected', [
    ('Lewis Carroll', 'Carroll, Lewis, 1832-1898', True, True),
    ('Carroll, Lewis', 'Lewis Carroll', True, True),
    ('Jacob Devlin', 'Jacob Devlin, Kenton Lee', False, True),
    ('Desired Researcher', 'Other Desired, Researcher Else', False, False),
    ('John Smith', 'J. Smith', True, False),
    ('J. Smith', 'John Smith', True, False),
    ('J. Smith', 'Smith, J.', True, True),
    ('John Smith', 'John Smithson', True, False),
    ('John Smith', 'John Smith Jr.', True, False),
    ('李明', '王李明', True, False),
    ('李明', '李明', True, True),
    ('Juan Pablo de la Cruz', 'de la Cruz, Juan Pablo', True, True),
    ('Desired Researcher', 'Researcher, Desired', False, False),
    ('Ada Example', 'Authors: Ada Example and Other Person', False, True),
])
def test_author_names_preserve_boundaries(requested, value, individual, expected):
    assert laya.author_matches(requested, {'value': value, 'individual': individual}) is expected


def test_subscription_prompt_does_not_prove_article_body(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'finish': 'The article body is visible.'})
    current = page([], title='Shared Title', text='Subscribe to access this article. Create your account and '
                   'choose a membership plan to continue reading. Terms and conditions apply.')
    current['headings'] = ['Shared Title']
    assert p.choose(current, [])['operation'] == 'BLOCKED'


def test_article_about_subscriptions_is_not_a_paywall(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'finish': 'The article body is visible.'})
    current = page([], title='Shared Title', text='This article studies subscription systems and their effects on '
                   'access to knowledge. It compares several publishing models and describes their limitations.')
    current['headings'] = ['Shared Title']
    assert p.choose(current, [])['operation'] == 'DONE'


def test_navigation_text_is_not_article_body(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'finish': 'The article body is visible.'})
    current = page([], title='Shared Title', text='Navigation and footer links ' * 12)
    current.update(headings=['Shared Title'], content_text='')
    assert p.choose(current, [])['operation'] != 'DONE'


@pytest.mark.parametrize('replacement', [[], [{'what': 'Language', 'value': 'French'}]])
def test_repair_cannot_remove_or_change_required_language(fake, monkeypatch, replacement):
    original = {'requirements': [{'what': 'Language', 'value': 'English'}],
                'open': 'Shared Title', 'finish': 'Read the English article body.'}
    p = policy(original)
    p.initial_plan = dict(original)
    monkeypatch.setattr(laya, 'plan_goal', lambda *_a, **_k: (
        {'requirements': replacement, 'open': 'Shared Title', 'finish': 'Open.'}, {}))
    p.make_plan(page([]), [], reason='No progress')
    assert p.plan['requirements'] == original['requirements']
    assert p.plan['finish'] == original['finish']
    assert p.repair_violation


def test_repair_can_remap_field_without_changing_its_value(fake, monkeypatch):
    original = {'requirements': [{'what': 'Language', 'value': 'English'}],
                'open': 'Shared Title', 'finish': 'Read the English article body.'}
    p = policy(original)
    p.initial_plan = dict(original)
    monkeypatch.setattr(laya, 'plan_goal', lambda *_a, **_k: (
        {'requirements': [{'what': 'Article language', 'value': 'English'}],
         'open': 'Shared Title', 'finish': 'Open.'}, {}))
    p.make_plan(page([]), [], reason='No progress')
    assert p.plan['requirements'][0]['what'] == 'Article language'
    assert p.plan['finish'] == original['finish']
    assert not p.repair_violation


def test_rejected_repairs_exhaust_budget_without_executing_weakened_plan(fake, monkeypatch):
    original = {'requirements': [{'what': 'Language', 'value': 'English'}],
                'open': 'Shared Title', 'finish': 'The English body is visible.'}
    p = policy(original)
    p.initial_plan = dict(original)
    p.repair_reason = 'No progress'
    planner = Mock(return_value=({'requirements': [], 'open': 'Shared Title', 'finish': 'Open.'}, {}))
    monkeypatch.setattr(laya, 'plan_goal', planner)
    current = page([], title='Shared Title', text='A different language article body. ' * 8)
    history = []
    first = p.choose(current, history)
    assert first['operation'] == 'WAIT'
    executed(history, first)
    second = p.choose(current, history)
    assert second['operation'] == 'BLOCKED'
    assert p.plan['requirements'] == original['requirements']
    assert planner.call_count == 2 and p.repairs == 2
    assert all(e['accepted'] is False for e in p.planning_events)
    assert 'Language' in second['stop_reason']


def test_query_refinement_is_allowed_but_search_category_is_preserved():
    original = {'requirements': [{'what': 'Search', 'value': 'Paper'},
                                {'what': 'Search category', 'value': 'English'}], 'open': 'Paper'}
    repaired = {'requirements': [{'what': 'Search', 'value': '"Paper"'},
                                {'what': 'Search category', 'value': 'English'}], 'open': 'Paper'}
    assert not laya.repair_violations(original, repaired)
    repaired['requirements'].pop()
    assert laya.repair_violations(original, repaired)


def test_repair_cannot_swap_route_or_remove_target():
    original = {'requirements': [{'what': 'From', 'value': 'Zurich'}, {'what': 'To', 'value': 'London'}],
                'open': 'Paper'}
    swapped = {'requirements': [{'what': 'From', 'value': 'London'}, {'what': 'To', 'value': 'Zurich'}],
               'open': 'Paper'}
    assert len(laya.repair_violations(original, swapped)) == 2
    assert laya.repair_violations(original, {**original, 'open': None})


def test_paywall_stops_without_repair_requests(fake, monkeypatch):
    p = policy({'requirements': [], 'open': 'Shared Title', 'finish': 'Read the body.'})
    p.initial_plan = dict(p.plan)
    planner = Mock(side_effect=AssertionError('No paywall repair'))
    monkeypatch.setattr(laya, 'plan_goal', planner)
    current = page([], title='Shared Title', text='Subscribe to read this article. ' * 6)
    d = p.choose(current, [])
    assert d['operation'] == 'BLOCKED' and 'subscription' in d['stop_reason']
    assert not p.repair_reason and not planner.called


@pytest.mark.parametrize('text,blocked', [
    ('Please sign in to continue reading this article.', True),
    ('请先登录后继续阅读完整文章。', True),
    ('Subscribe to our weekly newsletter.', False),
    ('This article explains why sites ask readers to sign in to read.', False),
])
def test_access_barrier_requires_explicit_access_instruction(text, blocked):
    assert bool(laya.access_barrier({'text': text})) is blocked


def test_missing_body_exhausts_one_wait_budget_without_replanning(fake, monkeypatch):
    p = policy({'requirements': [], 'open': 'Shared Title', 'finish': 'Read the body.'})
    p.initial_plan = dict(p.plan)
    current = page([], title='Shared Title', text='Navigation text ' * 12)
    current.update(headings=['Shared Title'], content_text='')
    history = []
    planner = Mock(side_effect=AssertionError('Missing body must not restart planning'))
    monkeypatch.setattr(laya, 'plan_goal', planner)
    for _ in range(laya.MAX_RESULT_WAITS + 1):
        d = p.choose(current, history)
        if d['operation'] == 'BLOCKED':
            break
        assert d['operation'] == 'WAIT'
        executed(history, d)
    assert d['operation'] == 'BLOCKED'
    assert len(history) == laya.MAX_RESULT_WAITS and not planner.called
    assert 'body' in d['stop_reason']


def test_body_arriving_within_wait_budget_can_finish(fake):
    p = policy({'requirements': [], 'open': 'Shared Title', 'finish': 'Read the body.'})
    current = page([], title='Shared Title', text='Loading')
    current.update(headings=['Shared Title'], content_text='')
    history = []
    executed(history, p.choose(current, history))
    current['content_text'] = 'A readable article paragraph with evidence. ' * 4
    assert p.choose(current, history)['operation'] == 'DONE'
