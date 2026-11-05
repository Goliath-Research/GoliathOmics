"""Pydantic row and parameter models for the omics semantic namespace."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DiseaseRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disease_id: int
    name: str
    parent_id: Optional[int] = None


class DiseaseListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: Optional[int] = None


class CustomerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    name: str
    organization_id: int


class CustomerListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: Optional[int] = None


class GroupRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: int
    name: str
    description: Optional[str] = None
    customer_id: Optional[int] = None
    sample_count: int = 0


class GroupListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: Optional[int] = None
    disease_id: Optional[int] = None


class GroupEnrollmentListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: Optional[int] = None


class SampleRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: int
    patient_id: int
    customer_id: Optional[int] = None
    disease_id: Optional[int] = None
    sample_name: Optional[str] = None
    age: Optional[float] = None
    bmi: Optional[float] = None


class SampleListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: Optional[int] = None
    disease_id: Optional[int] = None
    group_id: Optional[int] = None


class LabRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lab_id: int
    name: str


class LabListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
