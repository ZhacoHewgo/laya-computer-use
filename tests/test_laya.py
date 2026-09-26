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
    d = p.choose(page([], title="Confidence is not correctness · Site"), history)
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
    d = p.choose(page(FORM, title="Casa Flora · Forma"), [])
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
        ("one-way", "Round trip", None),
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
