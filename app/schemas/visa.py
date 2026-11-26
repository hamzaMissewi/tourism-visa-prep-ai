from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

CountryName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)
]

VisaPurpose = Literal[
    "tourism", "business", "work", "study", "family", "medical", "transit"
]


class VisaPlanCreateRequest(BaseModel):
    country: CountryName
    nationality: CountryName
    purpose: VisaPurpose
    travel_date: str | None = Field(default=None, max_length=40)
    duration_of_stay: str | None = Field(default=None, max_length=40)
    background: str | None = Field(default=None, max_length=2000)


class GeneratedDocument(BaseModel):
    """One checklist item as produced by the AI. Also the structured-output schema."""

    category: str
    title: str
    description: str
    is_required: bool


class GeneratedRoadmapStep(BaseModel):
    step: int
    title: str
    detail: str


class GeneratedVisaPlan(BaseModel):
    overview: str
    roadmap: list[GeneratedRoadmapStep]
    documents: list[GeneratedDocument]
    interview_tips: list[str]


class DocumentOut(BaseModel):
    id: int
    position: int
    category: str
    title: str
    description: str
    is_required: bool
    collected: bool
    model_config = {"from_attributes": True}


class VisaPlanDetail(BaseModel):
    id: int
    country: str
    nationality: str
    purpose: str
    travel_date: str | None
    duration_of_stay: str | None
    background: str | None
    overview: str
    roadmap: list[GeneratedRoadmapStep]
    interview_tips: list[str]
    created_at: datetime
    documents: list[DocumentOut]


class VisaPlanSummary(BaseModel):
    id: int
    country: str
    nationality: str
    purpose: str
    created_at: datetime
    documents_total: int
    documents_collected: int
    model_config = {"from_attributes": True}


class DocumentStatusUpdate(BaseModel):
    collected: bool
