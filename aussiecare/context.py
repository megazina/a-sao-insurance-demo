"""Per-request context (signed-in customer, active scenario) shared with tools."""

from __future__ import annotations

from contextvars import ContextVar

current_customer_id: ContextVar[str | None] = ContextVar("customer_id", default=None)
current_scenario: ContextVar[str] = ContextVar("scenario", default="Golden")
