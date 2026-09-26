import pytest

from examples.flights import verify as verify_flights
from examples.verification import python_chapter_checks

URL = "https://docs.python.org/3/tutorial/datastructures.html"


@pytest.mark.parametrize("parameter", ["x", "value, /"])
def test_python_chapter_checks_method_body_without_fixed_parameter_names(parameter):
    observed = {"heading": "5. Data Structures", "methods": [
        {"signature": f"list. append ({parameter})",
         "description": "Add an item to the end of the list. Similar to a[len(a):] = [x]."},
        {"signature": "list.extend(iterable, /)", "description": "Extend the list by appending all the items."},
    ]}
    assert python_chapter_checks(URL, observed)["passed"]
    assert not python_chapter_checks("https://example.com/3/tutorial/datastructures.html", observed)["passed"]


def test_python_chapter_rejects_index_and_method_names_without_explanations():
    index = {"heading": "Data Structures", "methods": []}
    assert not python_chapter_checks(URL, index)["passed"]
    index["methods"] = [{"signature": "list.append(x)"}, {"signature": "list.extend(iterable)"}]
    assert not python_chapter_checks(URL, index)["passed"]


def test_flight_origin_accessible_name_may_include_airport_but_value_must_match():
    page = {"url": "https://www.google.com/travel/flights/search", "text": "", "actions": [
        {"label": "Where from? Zürich ZRH", "role": "combobox", "value": "Zürich"},
    ]}
    assert verify_flights(page)["checks"]["origin"]
    assert not verify_flights(page)["passed"]  # An origin by itself is not a completed search.
    page["actions"][0]["value"] = "Hong Kong"
    assert not verify_flights(page)["checks"]["origin"]
