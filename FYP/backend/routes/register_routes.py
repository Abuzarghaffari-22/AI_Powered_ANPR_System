from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from auth import get_current_user
from database import get_db
import plate_store
from pipeline import normalize_plate, normalize_plate_ocr

router = APIRouter(prefix="/api/register", tags=["register"])


class QuickRegisterRequest(BaseModel):
    license_number: str
    owner_name:     str
    make:           Optional[str] = ""
    model:          Optional[str] = ""
    color:          Optional[str] = ""
    owner_cnic:     Optional[str] = ""
    dues:           str = "Clear"
    status:         str = "Authorized"

    @field_validator("dues")
    @classmethod
    def validate_dues(cls, v: str) -> str:
        if v.strip().lower() not in ("clear", "paid", "remaining", "", "none"):
            raise ValueError("dues must be Clear, Paid, or Remaining")
        return v.capitalize()

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v.strip().lower() not in ("authorized", "unauthorized"):
            raise ValueError("status must be Authorized or Unauthorized")
        return v.capitalize()


@router.post("")
async def quick_register(
    body: QuickRegisterRequest,
    conn = Depends(get_db),
    _    = Depends(get_current_user),
):
    norm = normalize_plate_ocr(body.license_number) or normalize_plate(body.license_number)
    if not norm or len(norm) < 5:
        raise HTTPException(400, "Invalid license number")

    if not body.owner_name.strip():
        raise HTTPException(400, "Owner name is required")

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id FROM vehicles WHERE license_normalized=%s LIMIT 1", (norm,))
        if cur.fetchone():
            raise HTTPException(409, f"Plate '{norm}' is already registered")

        dues_ok   = body.dues.strip().lower()   in ("clear", "paid", "", "none")
        status_ok = body.status.strip().lower() == "authorized"
        is_auth   = 1 if (dues_ok and status_ok) else 0

        cur.execute("""
            INSERT INTO vehicles
                (make, model, license_number, license_normalized,
                 color, owner_name, owner_cnic,
                 dues, status, is_authorized)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            body.make   or "",
            body.model  or "",
            body.license_number.strip(),
            norm,
            body.color      or "",
            body.owner_name.strip(),
            body.owner_cnic or "",
            body.dues,
            body.status,
            is_auth,
        ))
        new_id = cur.lastrowid

        cur.execute("SELECT * FROM vehicles WHERE id=%s", (new_id,))
        row = cur.fetchone()
    finally:
        cur.close()

    if not row:
        raise HTTPException(500, "Vehicle registered but could not be retrieved")

    plate_store.upsert(row)
    return {
        "message": "Vehicle registered successfully",
        "vehicle": row,
    }
