"""Provider-neutral helpdesk vocabulary shared by REST and MCP contracts."""

from enum import StrEnum


class SLATier(StrEnum):
    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"


class TicketCategory(StrEnum):
    EMAIL = "email"
    NETWORK = "network"
    HARDWARE = "hardware"
    SECURITY = "security"
    ONBOARDING = "onboarding"
    ALERTS = "alerts"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
