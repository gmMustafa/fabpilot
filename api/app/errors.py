"""Domain errors shared by every data layer (plant_data, rag.library).

Adapters translate them: REST -> 404 / 422, MCP -> ToolError (a message the model can act on).
"""


class NotFoundError(LookupError):
    """A machine, sensor or document that doesn't exist. REST: 404."""


class InvalidQueryError(ValueError):
    """A request that can't be answered as asked; the message says how to fix it. REST: 422."""
