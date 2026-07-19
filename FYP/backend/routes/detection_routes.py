import csv
import io
from datetime import date
from typing import Optional
import glob, os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth import get_current_user, require_admin
from database import get_db
from schemas import DetectionLogOut, PaginatedDetections

router = APIRouter(prefix="/api", tags=["detections"])


@router.get("/detections", response_model=PaginatedDetections)
async def list_detections(
    page:      int           = Query(1,  ge=1),
    per_page:  int           = Query(20, ge=1, le=100),
    plate:     Optional[str] = None,
    status:    Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    conn                     = Depends(get_db),
    _                        = Depends(get_current_user),
):
    cur    = conn.cursor(dictionary=True)
    where  = []
    params: list = []

    if plate:
        plate = plate[:60]
        where.append("detected_plate LIKE %s")
        params.append(f"%{plate}%")
    if status and status in ("authorized", "unauthorized", "unknown"):
        where.append("status = %s")
        params.append(status)
    if date_from:
        try:
            date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(400, "Invalid date_from format — use YYYY-MM-DD")
        where.append("detected_at >= %s")
        params.append(date_from + " 00:00:00")
    if date_to:
        try:
            date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(400, "Invalid date_to format — use YYYY-MM-DD")
        where.append("detected_at <= %s")
        params.append(date_to + " 23:59:59")

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    count_sql  = "SELECT COUNT(*) AS n FROM detection_logs" + (" " + clause if clause else "")
    select_sql = ("SELECT * FROM detection_logs" + (" " + clause if clause else "") +
                  " ORDER BY detected_at DESC LIMIT %s OFFSET %s")
    try:
        cur.execute(count_sql, params)
        row    = cur.fetchone()
        total  = row["n"] if row else 0
        offset = (page - 1) * per_page
        cur.execute(select_sql, params + [per_page, offset])
        items = cur.fetchall()
    finally:
        cur.close()

    return PaginatedDetections(
        items    = items,
        total    = total,
        page     = page,
        per_page = per_page,
        pages    = max(1, (total + per_page - 1) // per_page),
    )


@router.get("/alerts", response_model=PaginatedDetections)
async def list_alerts(
    page:     int = Query(1,  ge=1),
    per_page: int = Query(20, ge=1, le=100),
    conn          = Depends(get_db),
    _             = Depends(get_current_user),
):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS n FROM detection_logs WHERE status='unauthorized'")
        total  = cur.fetchone()["n"]
        offset = (page - 1) * per_page
        cur.execute(
            "SELECT * FROM detection_logs WHERE status='unauthorized' "
            "ORDER BY detected_at DESC LIMIT %s OFFSET %s",
            [per_page, offset],
        )
        items = cur.fetchall()
    finally:
        cur.close()
    return PaginatedDetections(items=items, total=total, page=page,
                               per_page=per_page,
                               pages=max(1, (total + per_page - 1) // per_page))


@router.delete("/detections/{detection_id}")
async def delete_detection(
    detection_id: int,
    conn          = Depends(get_db),
    _             = Depends(require_admin),
):
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM detection_logs WHERE id=%s", (detection_id,))
        affected = cur.rowcount
    finally:
        cur.close()
    if affected == 0:
        raise HTTPException(404, "Detection not found")
    return {"message": "deleted", "id": detection_id}


@router.get("/detections/export")
async def export_detections(
    plate:     Optional[str] = None,
    status:    Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    conn                     = Depends(get_db),
    _                        = Depends(get_current_user),
):
    cur    = conn.cursor(dictionary=True)
    where  = []
    params: list = []
    if plate:
        plate = plate[:60]
        where.append("detected_plate LIKE %s")
        params.append(f"%{plate}%")
    if status and status in ("authorized", "unauthorized", "unknown"):
        where.append("status = %s")
        params.append(status)
    if date_from:
        try:
            date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(400, "Invalid date_from format — use YYYY-MM-DD")
        where.append("detected_at >= %s")
        params.append(date_from + " 00:00:00")
    if date_to:
        try:
            date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(400, "Invalid date_to format — use YYYY-MM-DD")
        where.append("detected_at <= %s")
        params.append(date_to + " 23:59:59")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = ("SELECT id,detected_plate,matched_plate,owner_name,status,confidence,detected_at "
           "FROM detection_logs" + (" " + clause if clause else "") +
           " ORDER BY detected_at DESC LIMIT 50000")
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["id", "detected_plate", "matched_plate", "owner_name",
                    "status", "confidence", "detected_at"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=detections.csv"},
    )


@router.post("/test-detection")
async def test_detection(_ = Depends(get_current_user)):
    """Inject a sample car image into the live detection pipeline.
    The result will appear on the dashboard live feed within ~2 seconds."""
    import cv2
    try:
        import camera_worker_optimized as cw
    except ImportError:
        import camera_worker as cw

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base, "..", "data", "car_data", "car_data")
    img_path = None
    for ext in ("*.png", "*.jpg", "*.JPG", "*.jpeg", "*.JPEG"):
        matches = glob.glob(os.path.join(data_dir, ext))
        if matches:
            img_path = matches[0]
            break
    if img_path is None:
        raise HTTPException(404, "No sample car image found in data/car_data/car_data/")

    frame = cv2.imread(img_path)
    if frame is None:
        raise HTTPException(500, f"Could not read sample image: {os.path.basename(img_path)}")

    try:
        from pipeline_optimized import reset_pipeline_state_optimized
        reset_pipeline_state_optimized()
        import pipeline_optimized as _po
        _po._model_ready = True
    except Exception:
        pass
    try:
        from pipeline import reset_pipeline_state
        reset_pipeline_state()
    except Exception:
        pass

    state = getattr(cw, "_state", None)
    if state is None:
        raise HTTPException(503, "Camera worker not running")
    try:
        state.detection_queue.get_nowait()
    except Exception:
        pass
    try:
        state.detection_queue.put_nowait(frame)
    except Exception:
        pass

    return {"message": "Test frame injected — check dashboard live feed", "image": os.path.basename(img_path)}
