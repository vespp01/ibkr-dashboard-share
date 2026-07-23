"""Docs that hard-code API inventory counts must match FastAPI OpenAPI."""

from __future__ import annotations

from pathlib import Path

from ibkr_backend.api.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def _openapi_counts() -> tuple[int, dict[str, int]]:
    paths = create_app().openapi()["paths"]
    methods = [method.upper() for operations in paths.values() for method in operations]
    counts = {method: methods.count(method) for method in set(methods)}
    return len(methods), counts


def test_documented_api_inventory_counts_match_openapi():
    total, counts = _openapi_counts()
    get_count = counts.get("GET", 0)
    post_count = counts.get("POST", 0)

    readme = " ".join((ROOT / "README.md").read_text().split())
    assert (
        f"exposes {total} JSON endpoints "
        f"({get_count} GET read-only + {post_count} utility POST"
    ) in readme

    guide = (ROOT / "docs" / "puesta-en-marcha.html").read_text()
    assert f"expone {total} endpoints JSON" in guide
    assert f"{get_count} GET de solo-lectura + {post_count} POST de utilidad" in guide
