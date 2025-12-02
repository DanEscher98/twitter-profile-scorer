"""Base Pydantic models with strict validation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model with strict validation for all external data.

    All models that validate external input (API responses, DB rows, config files)
    should inherit from this class to ensure:
    - No type coercion (strict=True)
    - Immutable after creation (frozen=True)
    - Fail on unknown fields (extra="forbid")
    - Validate on assignment (validate_assignment=True)
    """

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
    )


class MutableModel(BaseModel):
    """Base model for internal mutable data structures.

    Use this for models that need to be modified after creation,
    like DTOs passed between tasks.
    """

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_assignment=True,
        use_enum_values=True,
    )
