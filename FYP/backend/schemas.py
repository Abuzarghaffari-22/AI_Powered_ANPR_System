from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type:   str
    username:     str
    role:         str


class VehicleCreate(BaseModel):
    vehicle_id_code: Optional[str] = Field("", max_length=20)
    make:            Optional[str] = Field("", max_length=60)
    model:           Optional[str] = Field("", max_length=60)
    license_number:  str           = Field(..., min_length=1, max_length=40)
    color:           Optional[str] = Field("", max_length=40)
    owner_name:      Optional[str] = Field("", max_length=120)
    owner_cnic:      Optional[str] = Field("", max_length=30)
    engine_number:   Optional[str] = Field("", max_length=60)
    chassis_number:  Optional[str] = Field("", max_length=60)
    dues:            str = "Clear"
    status:          str = "Authorized"
    image_filename:  Optional[str] = Field("", max_length=120)

    @field_validator("license_number")
    @classmethod
    def license_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("license_number must not be empty")
        return v.strip()

    @field_validator("dues")
    @classmethod
    def dues_valid(cls, v: str) -> str:
        if v.strip().lower() not in ("clear", "paid", "remaining", "", "none"):
            raise ValueError("dues must be Clear, Paid, or Remaining")
        mapping = {"clear": "Clear", "paid": "Paid", "remaining": "Remaining",
                   "": "Clear", "none": "Clear"}
        return mapping.get(v.strip().lower(), v.strip().capitalize())

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        if v.strip().lower() not in ("authorized", "unauthorized", "active"):
            raise ValueError("status must be Authorized or Unauthorized")
        mapping = {"authorized": "Authorized", "unauthorized": "Unauthorized",
                   "active": "Authorized"}
        return mapping.get(v.strip().lower(), v.strip().capitalize())


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                 int
    vehicle_id_code:    Optional[str]
    make:               Optional[str]
    model:              Optional[str]
    license_number:     str
    license_normalized: str
    color:              Optional[str]
    owner_name:         Optional[str]
    owner_cnic:         Optional[str]
    engine_number:      Optional[str] = None
    chassis_number:     Optional[str] = None
    dues:               str
    status:             str
    is_authorized:      int
    image_filename:     Optional[str]
    created_at:         Optional[datetime]
    updated_at:         Optional[datetime]


class DetectionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:             int
    detected_plate: Optional[str]
    matched_plate:  Optional[str]
    vehicle_id:     Optional[int]
    owner_name:     Optional[str]
    status:         str
    confidence:     Optional[float]
    image_path:     Optional[str]
    detected_at:    Optional[datetime]


class PaginatedDetections(BaseModel):
    items:    List[DetectionLogOut]
    total:    int
    page:     int
    per_page: int
    pages:    int


class PaginatedVehicles(BaseModel):
    items:    List[VehicleOut]
    total:    int
    page:     int
    per_page: int
    pages:    int


class StatsOut(BaseModel):
    total_vehicles:          int
    authorized_vehicles:     int
    unauthorized_vehicles:   int
    total_detections_today:  int
    authorized_today:        int
    unauthorized_today:      int
    total_detections_all:    int
