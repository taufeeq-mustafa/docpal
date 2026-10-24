from typing import Annotated, Literal

import pydantic
from pydantic import BaseModel

Unit = Annotated[float, pydantic.Field(ge=0, le=1)]
Box = Annotated[list[Unit], pydantic.Field(min_length=4, max_length=4)]  # page-relative [x0, y0, x1, y1]


# Agent 1 (explainer) output, streamed one JSON line at a time
class FormHeader(BaseModel):
    form_title: str
    purpose: str


class Field(BaseModel):
    label: str  # exactly as printed on the form
    evidence: str  # printed text that proves the field exists
    explanation: str
    required: bool | None  # None = the form doesn't say
    status: Literal["empty", "filled", "unclear"]
    you_need: list[str]  # info/documents the user must provide, never invented values


class Summary(BaseModel):
    checklist: list[str]
    warnings: list[str]


# Agent 2 (locator) input and output
class FieldRef(BaseModel):
    id: Annotated[int, pydantic.Field(ge=0)]
    label: Annotated[str, pydantic.Field(max_length=300)]
    evidence: Annotated[str, pydantic.Field(max_length=500)] = ""


class Location(BaseModel):
    id: int
    page: Annotated[int, pydantic.Field(ge=0)]
    box: Box  # where the answer goes
    label_box: Box | None = None  # only known for text-layer PDFs


class LocateResult(BaseModel):
    locations: list[Location]
    not_fields: list[int]  # ids the guardrails rejected (unit suffixes, duplicates of another field)


class FillValue(BaseModel):
    page: Annotated[int, pydantic.Field(ge=0)]
    box: Box
    value: Annotated[str, pydantic.Field(max_length=500)]
