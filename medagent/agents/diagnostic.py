from medagent.agents.base import AgentDefinition

DIAGNOSTIC_AGENT = AgentDefinition(
    "diagnostic_agent",
    "Diagnostic analysis worker",
    "Clinical pattern assessment, differential diagnosis, and diagnostic workup.",
    "Do not invent patient facts or present hypotheses as confirmed diagnoses.",
)
