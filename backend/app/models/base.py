from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Self
from uuid import uuid4

from bson import ObjectId
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from pydantic_core import core_schema


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UtcDateTime = Annotated[datetime, AfterValidator(normalize_datetime)]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def normalize_pan_id(value: str) -> str:
    if not isinstance(value, str):
        return value
    return value.strip().lower()


def normalize_identifier(value: str) -> str:
    if not isinstance(value, str):
        return value
    return value.strip()


class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any):
        return core_schema.no_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def validate(cls, value: Any) -> ObjectId:
        if not isinstance(value, ObjectId) and not ObjectId.is_valid(value):
            raise ValueError(f"invalid ObjectId: {value!r}")
        return ObjectId(value)


class BaseDocument(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    id: PyObjectId | None = Field(default=None, alias="_id", repr=False)

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="python",
        )
        if self.id is None:
            data.pop("_id", None)
        else:
            data["_id"] = self.id
        return data

    @classmethod
    def from_mongo(cls, document: dict[str, Any]) -> Self:
        return cls.model_validate(document)
