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
                "additionalProperties": False,
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


def validate_tool_arguments(name: str, arguments: object) -> None:
    """Validate the public flat object/string schemas without coercing input.

    String bounds are enforced only when declared; empty and unbounded strings
    remain valid otherwise. Errors never include model-supplied values or keys.
    """
    parameters = TOOL_SCHEMAS[name]["function"]["parameters"]
    prefix = f"invalid arguments for tool {name!r}: "
    if not isinstance(arguments, dict):
        raise TypeError(prefix + "expected an object")
    for field in parameters.get("required", []):
        if field not in arguments:
            raise TypeError(prefix + f"missing required field {field!r}")
    properties = parameters["properties"]
    if parameters.get("additionalProperties") is False and any(
        field not in properties for field in arguments
    ):
        raise TypeError(prefix + "additional fields are not allowed")
    for field, definition in properties.items():
        if field not in arguments:
            continue
        if definition["type"] != "string":
            raise TypeError(prefix + "unsupported field schema")
        value = arguments[field]
        if not isinstance(value, str):
            raise TypeError(prefix + f"field {field!r} must be a string")
        if "minLength" in definition and len(value) < definition["minLength"]:
            raise TypeError(prefix + f"field {field!r} is shorter than minLength")
        if "maxLength" in definition and len(value) > definition["maxLength"]:
            raise TypeError(prefix + f"field {field!r} exceeds maxLength")
