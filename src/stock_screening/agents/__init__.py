"""Pydantic AI agents for stock analysis."""

# Lazy imports to avoid circular dependencies
__all__ = [
    # Agent instances
    "screening_agent",
    "web_search_agent",
    # Main agent functions (public API)
    "route_query",
    "route_query_stream",
    "chat_stream",
]


def __getattr__(name: str):
    if name == "screening_agent":
        from stock_screening.agents.screening_agent import screening_agent
        return screening_agent
    if name == "web_search_agent":
        from stock_screening.agents.web_search_agent import web_search_agent
        return web_search_agent
    if name in ("route_query", "route_query_stream", "chat_stream"):
        from stock_screening.agents import main_agent as _main
        return getattr(_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
