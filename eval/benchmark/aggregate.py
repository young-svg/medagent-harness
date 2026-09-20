from __future__ import annotations


def aggregate(records: list[dict[str, float]]) -> dict[str, float]:
    keys = sorted({key for record in records for key in record})
    return (
        {key: sum(record.get(key, 0.0) for record in records) / len(records) for key in keys}
        if records
        else {}
    )
