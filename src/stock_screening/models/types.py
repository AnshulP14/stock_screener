"""Single source of truth for shared types and enums."""

from enum import Enum
from typing import Literal


class AgentType(str, Enum):
    """Agent types for routing decisions."""

    SCREENING = "screening"
    WEB_SEARCH = "web_search"


# Log levels for application logging
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
