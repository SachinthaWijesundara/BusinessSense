from __future__ import annotations

from typing import Any


class WorkflowError(Exception):
    """A request the pipeline or a business rule refuses, with the HTTP status to report."""

    def __init__(self, status_code: int, error_type: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"error_type": self.error_type, "message": self.message, **self.extra}
