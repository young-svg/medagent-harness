from __future__ import annotations


def offline_unavailable(**_: object) -> dict[str, object]:
    return {"success": False, "error": "external clinical backend not configured", "items": []}
