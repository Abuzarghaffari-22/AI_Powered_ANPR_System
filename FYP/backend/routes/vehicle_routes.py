from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_admin
from database import get_db
import plate_store
from pipeline import normalize_plate
from schemas import VehicleCreate, VehicleOut, PaginatedVehicles

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


def _is_auth(dues: str, status: str) -> int:
    return 1 if (
        dues.strip().lower() in ("clear", "paid", "", "none")
        and status.strip().lower() in ("authorized", "active")
    ) else 0


def _invalidate_plate_cache(normalized: str) -> None:
    plate_store.invalidate(normalized)


@router.get("", response_model=PaginatedVehicles)
async def list_vehicles(
    page:          int           = Query(1,  ge=1),
    per_page:      int           = Query(20, ge=1, le=100),
    search:        Optional[str] = None,
    is_authorized: Optional[int] = None,
    conn                         = Depends(get_db),
    _                            = Depends(get_current_user),
):
    cur    = conn.cursor(dictionary=True)
    where  = []
    params = []
    if search:
        search = search[:100]
        where.append("(license_normalized LIKE %s OR owner_name LIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    if is_authorized is not None:
        where.append("is_authorized=%s")
        params.append(is_authorized)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * per_page
    count_sql  = "SELECT COUNT(*) AS n FROM vehicles" + (" " + clause if clause else "")
    select_sql = ("SELECT * FROM vehicles" + (" " + clause if clause else "") +
                  " ORDER BY id DESC LIMIT %s OFFSET %s")
    try:
        cur.execute(count_sql, params)
        total = (cur.fetchone() or {}).get("n", 0)
        cur.execute(select_sql, params + [per_page, offset])
        rows = cur.fetchall()
    finally:
        cur.close()
    return PaginatedVehicles(
        items    = rows,
        total    = total,
        page     = page,
        per_page = per_page,
        pages    = max(1, (total + per_page - 1) // per_page),
    )


@router.get("/{vehicle_id}", response_model=VehicleOut)
async def get_vehicle(vehicle_id: int, conn=Depends(get_db),
                      _=Depends(get_current_user)):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM vehicles WHERE id=%s", (vehicle_id,))
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        raise HTTPException(404, "Vehicle not found")
    return row


@router.post("", response_model=VehicleOut, status_code=201)
async def create_vehicle(body: VehicleCreate, conn=Depends(get_db),
                         _=Depends(require_admin)):
    norm = normalize_plate(body.license_number)
    if not norm:
        raise HTTPException(400, "Invalid license number")
    is_auth = _is_auth(body.dues, body.status)
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM vehicles WHERE license_normalized=%s LIMIT 1", (norm,))
        if cur.fetchone():
            raise HTTPException(409, f"Plate '{norm}' is already registered")
        cur.execute("""
            INSERT INTO vehicles
                (vehicle_id_code,make,model,license_number,license_normalized,
                 color,owner_name,owner_cnic,engine_number,chassis_number,
                 dues,status,is_authorized,image_filename)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (body.vehicle_id_code, body.make, body.model,
              body.license_number, norm, body.color, body.owner_name,
              body.owner_cnic, body.engine_number, body.chassis_number,
              body.dues, body.status, is_auth, body.image_filename))
        new_id = cur.lastrowid
        cur.execute("SELECT * FROM vehicles WHERE id=%s", (new_id,))
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        raise HTTPException(500, "Vehicle created but could not be retrieved")
    plate_store.upsert(row)
    return row


@router.put("/{vehicle_id}", response_model=VehicleOut)
async def update_vehicle(vehicle_id: int, body: VehicleCreate,
                         conn=Depends(get_db), _=Depends(require_admin)):
    norm = normalize_plate(body.license_number)
    if not norm:
        raise HTTPException(400, "Invalid license number")
    is_auth = _is_auth(body.dues, body.status)
    cur     = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM vehicles WHERE id=%s", (vehicle_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(404, "Vehicle not found")
        old_norm = existing.get("license_normalized", "")
        cur.execute("""
            UPDATE vehicles SET
                vehicle_id_code=%s,make=%s,model=%s,
                license_number=%s,license_normalized=%s,
                color=%s,owner_name=%s,owner_cnic=%s,
                engine_number=%s,chassis_number=%s,
                dues=%s,status=%s,is_authorized=%s,image_filename=%s
            WHERE id=%s
        """, (body.vehicle_id_code, body.make, body.model,
              body.license_number, norm, body.color, body.owner_name,
              body.owner_cnic, body.engine_number, body.chassis_number,
              body.dues, body.status, is_auth,
              body.image_filename, vehicle_id))
        cur.execute("SELECT * FROM vehicles WHERE id=%s", (vehicle_id,))
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        raise HTTPException(500, "Vehicle updated but could not be retrieved")
    if old_norm and old_norm.upper() != norm.upper():
        plate_store.invalidate(old_norm)
    plate_store.upsert(row)
    return row


@router.delete("/{vehicle_id}")
async def delete_vehicle(vehicle_id: int, conn=Depends(get_db),
                         _=Depends(require_admin)):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT license_normalized FROM vehicles WHERE id=%s", (vehicle_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Vehicle not found")
        norm = row["license_normalized"]
        cur.execute("DELETE FROM vehicles WHERE id=%s", (vehicle_id,))
    finally:
        cur.close()
    _invalidate_plate_cache(norm)
    return {"message": "deleted", "id": vehicle_id}
