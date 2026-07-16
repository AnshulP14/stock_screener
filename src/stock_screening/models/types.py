"""Shared types and enums."""

from enum import Enum
from typing import Literal


class AgentType(str, Enum):
    SCREENING = "screening"
    WEB_SEARCH = "web_search"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
