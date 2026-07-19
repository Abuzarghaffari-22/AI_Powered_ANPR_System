from datetime import date

from fastapi import APIRouter, Depends

from auth import get_current_user, require_admin
from database import get_db
import plate_store
from schemas import StatsOut

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
async def get_stats(conn=Depends(get_db), _=Depends(get_current_user)):
    cur   = conn.cursor(dictionary=True)
    today = date.today().isoformat()
    try:
        cur.execute("""
            SELECT
                COUNT(*)           AS total_vehicles,
                SUM(is_authorized = 1) AS authorized_vehicles
            FROM vehicles
        """)
        vrow    = cur.fetchone() or {}
        total_v = int(vrow.get("total_vehicles", 0) or 0)
        auth_v  = int(vrow.get("authorized_vehicles", 0) or 0)

        today_start = today + " 00:00:00"
        today_end   = today + " 23:59:59.999999"
        cur.execute("""
            SELECT
                COUNT(*) AS total_det,
                SUM(detected_at BETWEEN %s AND %s) AS det_today,
                SUM(status = 'authorized' AND detected_at BETWEEN %s AND %s) AS auth_today
            FROM detection_logs
        """, (today_start, today_end, today_start, today_end))
        drow      = cur.fetchone() or {}
        total_det = int(drow.get("total_det", 0) or 0)
        det_today = int(drow.get("det_today", 0) or 0)
        auth_t    = int(drow.get("auth_today", 0) or 0)
    finally:
        cur.close()

    return StatsOut(
        total_vehicles         = total_v,
        authorized_vehicles    = auth_v,
        unauthorized_vehicles  = total_v - auth_v,
        total_detections_today = det_today,
        authorized_today       = auth_t,
        unauthorized_today     = det_today - auth_t,
        total_detections_all   = total_det,
    )


@router.post("/reload-store", tags=["admin"])
async def reload_plate_store(conn=Depends(get_db), _=Depends(require_admin)):
    n = plate_store.load(conn)
    return {"message": f"PlateStore reloaded — {n} vehicles in RAM", "count": n}
