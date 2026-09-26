"""Independent outcome checks for live examples; no model or browser configuration."""

from urllib.parse import urlparse


def python_chapter_checks(url, observed):
    """Require the real chapter and method descriptions, independent of parameter names."""
    methods = observed.get("methods", [])

    def explained(name, description):
        return any(
            "".join(method.get("signature", "").split()).startswith(f"list.{name}(")
            and description in " ".join(method.get("description", "").split())
            for method in methods
        )

    parsed = urlparse(url)
    checks = {
        "url": parsed.hostname == "docs.python.org" and parsed.path == "/3/tutorial/datastructures.html",
        "heading": "Data Structures" in (observed.get("heading") or ""),
        "body": explained("append", "Add an item to the end of the list")
        and explained("extend", "Extend the list by appending"),
    }
    return {"passed": all(checks.values()), "checks": checks, "observed": observed}


def attention_paper_checks(observed):
    """Verify this example's specific paper, including abstract content rather than a UI caption."""
    parsed = urlparse(observed.get("url", ""))
    path = parsed.path.rstrip("/")
    article = path.removeprefix("/abs/")
    base, _, version = article.partition("v")
    text = " ".join(observed.get("text", "").split())
    headings = [" ".join(h.split()).removeprefix("Title:").strip() for h in observed.get("headings", [])]
    checks = {
        "url": parsed.hostname == "arxiv.org" and path.startswith("/abs/")
        and base == "1706.03762" and (not version or version.isdigit()),
        "heading": "Attention Is All You Need" in headings,
        "authors": "Ashish Vaswani" in text and "Noam Shazeer" in text,
        "abstract": "The dominant sequence transduction models" in text
        and "the Transformer, based solely on attention mechanisms" in text
        and "English constituency parsing" in text,
    }
    return {"passed": all(checks.values()), "checks": checks, "observed": observed}
