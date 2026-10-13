"""Lifespan resources available to tools and the audit/policy middleware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispatch.mcp_server.config import MCPSettings
from dispatch.mcp_server.providers.base import TicketProvider

if TYPE_CHECKING:
    from dispatch.mcp_server.audit import AuditLog
    from dispatch.mcp_server.services.reports import ReportService
    from dispatch.mcp_server.services.similarity import SimilarityService


@dataclass(frozen=True)
class ServerState:
    provider: TicketProvider
    settings: MCPSettings
    audit: AuditLog
    similarity: SimilarityService | None = None
    reports: ReportService | None = None
