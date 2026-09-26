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
