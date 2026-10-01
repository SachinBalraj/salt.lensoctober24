from pydantic import Field, field_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    utcnow,
)


class User(BaseDocument):
    user_id: str = Field(
        default_factory=lambda: new_id("user"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str | None = Field(default=None, max_length=254)
    role: str = Field(default="operator", pattern=r"^(operator|researcher|admin)$")
    password_hash: str = Field(min_length=1, max_length=512)
    is_active: bool = True
    created_at: UtcDateTime = Field(default_factory=utcnow)
    updated_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("user_id", "username", mode="before")
    @classmethod
    def _normalize_identifiers(cls, value):
        return normalize_identifier(value)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value.count("@") != 1 or value.startswith("@") or value.endswith("@"):
            raise ValueError("email must be a valid address")
        local, domain = value.split("@", 1)
        if not local or not domain or "." not in domain or " " in value:
            raise ValueError("email must be a valid address")
        return value
