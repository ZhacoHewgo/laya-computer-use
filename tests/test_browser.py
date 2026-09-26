"""Navigation can interrupt reads, but must never replay browser mutations."""

from unittest.mock import Mock

import pytest

from laya_ultrafast import browser


def test_startup_retries_only_read_after_context_replacement(monkeypatch):
    calls = []
    reads = iter([RuntimeError("Execution context was destroyed"), False, True])

    def cdp(method, **kwargs):
        calls.append(method)
        if method == "Target.createTarget":
            return {"targetId": "owned"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session"}
        if method == "Runtime.evaluate":
            result = next(reads)
            if isinstance(result, Exception):
                raise result
            return {"result": {"value": result}}
        return {}

    monkeypatch.setattr(browser, "ensure_daemon", lambda: None)
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", lambda _: None)
    b = browser.Browser("https://example.test/")
    assert calls.count("Page.navigate") == 1
    assert calls.count("Runtime.evaluate") == 3
    b.close()
    assert calls.count("Target.closeTarget") == 1


def test_failed_navigation_closes_owned_target(monkeypatch):
    cdp = Mock(side_effect=[{"targetId": "owned"}, {"sessionId": "s"}, {}, {},
                            {"errorText": "net::ERR_NAME_NOT_RESOLVED"}, {}])
    monkeypatch.setattr(browser, "ensure_daemon", lambda: None)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        browser.Browser("https://example.invalid/")
    assert cdp.call_args.args[0] == "Target.closeTarget"
    assert cdp.call_args.kwargs["targetId"] == "owned"


def test_genuine_script_error_is_not_retriable_stale_page(monkeypatch):
    call = Mock(return_value={"exceptionDetails": {
        "exception": {"description": "TypeError: missing property"}, "text": "Uncaught",
    }})
    monkeypatch.setattr(browser, "cdp", call)
    with pytest.raises(RuntimeError, match="TypeError: missing property"):
        browser.browser_operation({"operation": "observe", "session": "s", "screenshot": False})
    assert call.call_count == 1


def test_context_loss_during_select_is_not_retriable():
    call = Mock(side_effect=RuntimeError("Execution context was destroyed"))
    with pytest.raises(RuntimeError, match="Dropdown execution.*Execution context"):
        browser.evaluate_script(call, "mutation", mutation=True)
    assert call.call_count == 1


def test_search_enter_is_one_observed_keyboard_submission(monkeypatch):
    def result(method, **kwargs):
        return {"result": {"value": {"x": 10, "y": 20}}} if method == "Runtime.evaluate" else {}

    call = Mock(side_effect=result)
    monkeypatch.setattr(browser, "cdp", call)
    browser.browser_operation({"operation": "act", "session": "s",
                               "action": {"id": "enter", "node": 1, "kind": "enter"}})
    keys = [c.kwargs for c in call.call_args_list if c.args[0] == "Input.dispatchKeyEvent"]
    assert [(k["type"], k["key"]) for k in keys] == [("keyDown", "Enter"), ("keyUp", "Enter")]


def test_enter_failure_is_not_retried(monkeypatch):
    def result(method, **kwargs):
        if method == "Input.dispatchKeyEvent":
            raise RuntimeError("Connection interrupted")
        return {"result": {"value": {"x": 10, "y": 20}}} if method == "Runtime.evaluate" else {}

    call = Mock(side_effect=result)
    monkeypatch.setattr(browser, "cdp", call)
    with pytest.raises(RuntimeError, match="Connection interrupted"):
        browser.browser_operation({"operation": "act", "session": "s",
                                   "action": {"id": "enter", "node": 1, "kind": "enter"}})
    assert sum(c.args[0] == "Input.dispatchKeyEvent" for c in call.call_args_list) == 1


def test_back_uses_actual_history_entry_once(monkeypatch):
    call = Mock(side_effect=[{"currentIndex": 1, "entries": [{"id": 11}, {"id": 20}]}, {}])
    monkeypatch.setattr(browser, "cdp", call)
    browser.browser_operation({"operation": "act", "session": "s", "action": {"id": "back", "kind": "back"}})
    assert call.call_args.args == ("Page.navigateToHistoryEntry",)
    assert call.call_args.kwargs["entryId"] == 11
    assert call.call_count == 2


def test_interrupted_back_is_not_replayed(monkeypatch):
    call = Mock(side_effect=[{"currentIndex": 1, "entries": [{"id": 11}, {"id": 20}]}, RuntimeError("Disconnected")])
    monkeypatch.setattr(browser, "cdp", call)
    with pytest.raises(RuntimeError, match="Disconnected"):
        browser.browser_operation({"operation": "act", "session": "s", "action": {"id": "back", "kind": "back"}})
    assert call.call_count == 2
