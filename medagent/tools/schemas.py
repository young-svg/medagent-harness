from __future__ import annotations

TOOL_SCHEMAS: dict[str, dict[str, object]] = {
    name: {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {argument: {"type": "string"}},
                "required": [argument],
            },
        },
    }
    for name, description, argument in (
        ("analyze_symptoms", "Analyze symptom patterns.", "symptoms"),
        ("assess_risk", "Assess clinical urgency.", "symptoms"),
        ("clinical_guideline", "Search configured guideline evidence.", "query"),
        ("disease_code", "Search configured disease classifications.", "disease_name"),
        ("recommend_lifestyle", "Retrieve lifestyle guidance.", "diagnosis"),
        ("deep_research", "Search configured research evidence.", "query"),
        ("search_history", "Search this session's context.", "session_id"),
        ("search_knowledge", "Search the configured clinical corpus.", "query"),
    )
}
