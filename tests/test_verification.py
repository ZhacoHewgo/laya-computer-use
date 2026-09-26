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


def test_attention_paper_requires_identity_and_abstract_not_a_caption():
    from examples.verification import attention_paper_checks

    observed = {"url": "https://arxiv.org/abs/1706.03762v7", "headings": ["Attention Is All You Need"],
                "text": "Ashish Vaswani, Noam Shazeer. The dominant sequence transduction models ... "
                        "the Transformer, based solely on attention mechanisms ... English constituency parsing"}
    assert attention_paper_checks(observed)["passed"]
    assert not attention_paper_checks({**observed, "url": "https://arxiv.org/abs/2512.11254"})["passed"]
    caption_only = {**observed, "text": "Ashish Vaswani, Noam Shazeer. Abstract: Transformer"}
    assert not attention_paper_checks(caption_only)["passed"]


def test_stability_bert_checks_cannot_pass_on_a_search_listing():
    from examples.evaluate_stability import BERT, verify_search_case

    observed = {"url": "https://arxiv.org/abs/1810.04805v2", "headings": [BERT],
                "text": "Jacob Devlin and Kenton Lee. A bidirectional language representation model can be fine-tuned."}
    assert verify_search_case("bert_en", observed)["passed"]
    assert verify_search_case("bert_zh", observed)["passed"]
    assert not verify_search_case("bert_en", {**observed, "url": "https://arxiv.org/search/"})["passed"]
    assert not verify_search_case("bert_en", {**observed, "headings": ["BERT overview"]})["passed"]


def test_stability_ada_checks_require_article_body_and_correct_host():
    from examples.evaluate_stability import verify_search_case

    observed = {"url": "https://en.wikipedia.org/wiki/Ada_Lovelace", "headings": ["Ada Lovelace"],
                "text": "Charles Babbage and the Analytical Engine."}
    assert verify_search_case("ada_zh", observed)["passed"]
    assert not verify_search_case("ada_zh", {**observed, "url": "https://example.org/wiki/Ada_Lovelace"})["passed"]
    assert not verify_search_case("ada_zh", {**observed, "text": "Loading"})["passed"]
