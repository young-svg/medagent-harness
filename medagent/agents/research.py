from medagent.agents.base import AgentDefinition

RESEARCH_AGENT = AgentDefinition(
    "research_agent",
    "Evidence review worker",
    "Summarize only admitted retrieved references relevant to the subtask.",
    "Do not claim a retrieved document proves a patient-specific conclusion.",
)
