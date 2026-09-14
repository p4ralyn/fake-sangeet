from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    """The single error shape for the whole API (LLD-v1 §13)."""

    error: ErrorDetail
