# ANPR System — Technical Documentation

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Backend](#3-backend)
4. [Frontend](#4-frontend)
5. [AI Pipeline](#5-ai-pipeline)
6. [Database Schema](#6-database-schema)
7. [API Reference](#7-api-reference)
8. [Security](#8-security)
9. [Configuration](#9-configuration)
10. [Deployment](#10-deployment)

---

## 1. System Overview

The ANPR (Automatic Number Plate Recognition) system is a real-time vehicle monitoring platform built for Pakistani toll plazas and security checkpoints. It combines a YOLOv11 object detection model with Tesseract OCR to read license plates from a live camera feed, look them up in a vehicle registry, and display results on a web dashboard.

**Key capabilities:**
- Real-time plate detection at 30+ FPS via WebSocket streaming
- Pakistani plate format support (ABC-1234, VR287-13, MNA2206-08, etc.)
- Authorized / unauthorized vehicle classification
- Admin dashboard with live feed, detection logs, vehicle registry, and alerts
- CSV export of detection logs
- Mobile camera support via DroidCam

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Browser Client                       │
│              Next.js 14 (TypeScript + Tailwind)          │
│   Login → Dashboard → Live Feed (WebSocket) + CRUD UI   │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / WebSocket
┌────────────────────────▼────────────────────────────────┐
│                  FastAPI Backend (Python)                 │
│  Auth · Vehicles · Detections · Stats · Stream · Register│
│                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │ Camera Worker│   │  AI Pipeline │   │ Plate Store │  │
│  │  (Thread)    │──▶│ YOLO + OCR   │──▶│  (RAM cache)│  │
│  └──────────────┘   └──────────────┘   └──────┬──────┘  │
└──────────────────────────────────────────────┼──────────┘
                                               │ SQL
┌──────────────────────────────────────────────▼──────────┐
│                    MySQL 8 Database                       │
│           users · vehicles · detection_logs              │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Backend

### Stack
| Component | Technology |
|-----------|-----------|
| Framework | FastAPI 0.110+ |
| Server | Uvicorn (single worker) |
| Database driver | mysql-connector-python |
| Auth | JWT (python-jose) + bcrypt |
| AI | Ultralytics YOLOv11 + Tesseract OCR |
| Image processing | OpenCV, Pillow, NumPy |

### File Structure
```
FYP/backend/
├── main.py                    # App entry point, lifespan, CORS, middleware
├── auth.py                    # JWT, bcrypt, rate limiting, RBAC
├── database.py                # Connection pool, get_db dependency
├── pipeline.py                # Full ANPR pipeline (YOLO + OCR + DB lookup)
├── pipeline_optimized.py      # High-performance variant
├── camera_worker.py           # Background thread: captures frames, runs pipeline
├── camera_worker_optimized.py # Optimized variant with frame queue
├── plate_store.py             # In-memory vehicle index (15+ fuzzy strategies)
├── schemas.py                 # Pydantic request/response models
├── routes/
│   ├── auth_routes.py         # POST /api/auth/login, /login/json, /me, /change-password
│   ├── detection_routes.py    # GET/DELETE /api/detections, GET /api/detections/export
│   ├── vehicle_routes.py      # CRUD /api/vehicles (admin-only write)
│   ├── stats_routes.py        # GET /api/stats, POST /api/stats/reload-store
│   ├── stream_routes.py       # POST /api/stream/ticket, WS /api/stream
│   └── register_routes.py     # POST /api/register (quick register from live detection)
├── models/
│   └── yolov11_plate_detection.pt
├── migrations/                # Alembic DB migrations
├── tests/
│   ├── conftest.py            # Shared fixtures, env setup, camera stub
│   └── test_api.py            # 30+ smoke tests
├── .env                       # Environment configuration
└── requirements.txt
```

### Camera Worker
The camera worker runs in a background thread. It:
1. Opens the camera (local webcam or DroidCam IP)
2. Reads frames in a loop
3. Every `PROCESS_EVERY_N_FRAMES` frames, sends the frame to the AI pipeline
4. Broadcasts the annotated JPEG + detection JSON to all connected WebSocket clients

### Plate Store
An in-memory index loaded at startup from the `vehicles` table. Lookup strategies (in order):
1. Exact normalized match
2. Exact stripped (no-dash) match
3. Edit-distance-1
4. OCR confusion map (O↔0, I↔1, S↔5, B↔8, G↔6, etc.)
5. Prefix match (year suffix dropped by OCR)
6. Edit-distance-2 (same first-3 prefix)
7. Digit suffix index
8. Substring match
9. Double confusion (two OCR errors)

---

## 4. Frontend

### Stack
| Component | Technology |
|-----------|-----------|
| Framework | Next.js 14 (Pages Router) |
| Language | TypeScript |
| Styling | Tailwind CSS + CSS variables |
| Fonts | Sora + JetBrains Mono |
| State | React hooks (no external state library) |

### Pages
| Route | Description |
|-------|-------------|
| `/` | Redirects to `/login` |
| `/login` | Authentication page with system health indicators |
| `/dashboard` | Main dashboard (Overview, Detections, Vehicles, Alerts) |

### Dashboard Sections
- **Overview** — Live camera feed (WebSocket), stats cards, last detection card
- **Detections** — Paginated detection log table with filters + CSV export
- **Vehicles** — Vehicle registry CRUD with search and pagination
- **Alerts** — Unauthorized-only detection log

### WebSocket Flow
1. Frontend calls `POST /api/stream/ticket` with JWT → receives a one-time ticket
2. Opens `ws://host:8000/api/stream?ticket=<ticket>`
3. Receives JSON frames: `{ type: "frame", frame: "<base64 JPEG>", detection: {...}, is_new: bool }`
4. Renders JPEG directly into `<img>` tag for zero-latency display

---

## 5. AI Pipeline

### Detection Flow (per frame)
```
Frame
  │
  ▼
YOLO inference (640px) ──miss──▶ native-res retry ──miss──▶ 1280px retry
  │ hit
  ▼
NMS deduplication (IoU > 0.45)
  │
  ▼
Plate crop + angle-aware padding
  │
  ▼
Image quality checks (blur, low-light)
  │
  ▼
Preprocessing (dewarp → upscale → bilateral → USM → border-remove → deskew)
  │
  ▼
7 image variants (gray, inverted, CLAHE, Otsu, adaptive, CLAHE4, morph)
  │
  ▼
OCR (tesserocr C++ API preferred, pytesseract fallback)
25+ PSM/OEM combos, early exit at 0.90 score
  │
  ▼
normalize_plate_ocr() → canonical plate string
  │
  ▼
_score_plate() → confidence (0.0–1.0)
  │
  ▼
Multi-frame voting buffer (STABLE_FRAMES reads required)
  │
  ▼
plate_store.lookup() → vehicle row + auth status
  │
  ▼
draw_result() → annotated frame
  │
  ▼
save_log() (async, ThreadPoolExecutor)
```

### OCR Confidence Scoring
| Score | Meaning |
|-------|---------|
| 0.90 | Standard plate (ABC-1234, APD-987) |
| 0.90 | Year-format plate (VR287-13, MNA2206-08) |
| 0.70 | Diplomatic plate (CD-1234) |
| 0.68 | 2-char prefix + 4-digit suffix (borderline) |
| 0.50 | Single-alpha prefix (G-1234) |
| < MIN_OCR_CONF | Rejected |

---

## 6. Database Schema

### `users`
| Column | Type | Notes |
|--------|------|-------|
| id | INT PK | |
| username | VARCHAR(60) UNIQUE | |
| password_hash | VARCHAR(255) | bcrypt |
| role | ENUM('admin','viewer') | |
| last_login | DATETIME | |

### `vehicles`
| Column | Type | Notes |
|--------|------|-------|
| id | INT PK | |
| vehicle_id_code | VARCHAR(20) | |
| make / model | VARCHAR(60) | |
| license_number | VARCHAR(40) | Raw input |
| license_normalized | VARCHAR(40) UNIQUE | Canonical form (ABC-1234) |
| license_stripped | VARCHAR(40) GENERATED | No dashes (ABC1234) |
| color / owner_name / owner_cnic | VARCHAR | |
| dues | ENUM('Clear','Paid','Remaining') | |
| status | VARCHAR(20) | 'Authorized' / 'Unauthorized' |
| is_authorized | TINYINT(1) | Computed from dues + status |
| created_at / updated_at | DATETIME | |

**Indexes:** `uq_plate_norm`, `idx_license_stripped`, `idx_auth_covering`

### `detection_logs`
| Column | Type | Notes |
|--------|------|-------|
| id | INT PK | |
| detected_plate | VARCHAR(40) | Raw OCR output |
| matched_plate | VARCHAR(40) | Matched DB plate |
| vehicle_id | INT FK | NULL if not found |
| owner_name | VARCHAR(120) | Denormalized for speed |
| status | VARCHAR(20) | authorized / unauthorized |
| confidence | DECIMAL(5,4) | OCR confidence |
| image_path | VARCHAR(255) | |
| detected_at | DATETIME | |

---

## 7. API Reference

### Authentication
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/login` | — | Form login (Swagger UI) |
| POST | `/api/auth/login/json` | — | JSON login (frontend) |
| GET | `/api/auth/me` | Bearer | Current user info |
| POST | `/api/auth/change-password` | Bearer | Change own password |

### Vehicles (write = admin only)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/vehicles` | Bearer | List with pagination + search |
| GET | `/api/vehicles/{id}` | Bearer | Single vehicle |
| POST | `/api/vehicles` | Admin | Create vehicle |
| PUT | `/api/vehicles/{id}` | Admin | Update vehicle |
| DELETE | `/api/vehicles/{id}` | Admin | Delete vehicle |

### Detections
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/detections` | Bearer | Paginated list with filters |
| GET | `/api/detections/export` | Bearer | Download CSV |
| DELETE | `/api/detections/{id}` | Bearer | Delete log entry |
| GET | `/api/alerts` | Bearer | Unauthorized-only list |

### Stats & Stream
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/stats` | Bearer | Dashboard counters |
| POST | `/api/stats/reload-store` | Admin | Reload plate store from DB |
| POST | `/api/stream/ticket` | Bearer | Issue one-time WS ticket |
| WS | `/api/stream?ticket=` | Ticket | Live JPEG + detection stream |
| GET | `/api/health` | — | System health check |

---

## 8. Security

### Authentication
- JWT HS256 tokens, configurable expiry (default 60 min)
- bcrypt password hashing (cost factor 12)
- Rate limiting: 5 failed attempts per 5-minute window → 5-minute lockout
- Password complexity: 12+ chars, upper, lower, digit, symbol

### Role-Based Access Control
- `viewer` role: read-only (GET endpoints)
- `admin` role: full access including vehicle writes and store reload
- Enforced via `require_admin` FastAPI dependency

### WebSocket Security
- Raw JWT never sent in URL (ticket-based auth)
- One-time tickets expire in 30 seconds
- Tickets are consumed on first use

### Transport
- CORS restricted to configured origins
- Request body size limit: 2 MB
- Production: nginx TLS termination (see `deploy/nginx.conf`)

---

## 9. Configuration

All settings live in `FYP/backend/.env`:

```env
# JWT
SECRET_KEY=<64-char hex>
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Database
DB_HOST=localhost
DB_USER=anpr_user
DB_PASSWORD=<strong password>
DB_NAME=anpr_db
DB_PORT=3306
DB_POOL_SIZE=25

# Camera
CAMERA_INDEX=0          # Local webcam index
CAMERA_IP=              # DroidCam IP (leave blank for webcam)

# YOLO
CONF_THRESHOLD=0.45     # Min YOLO confidence to attempt OCR
YOLO_INPUT_W=640        # YOLO inference resolution

# OCR
MIN_OCR_CONF=0.70       # Min plate score to accept
BLUR_THRESHOLD=18.0     # Laplacian variance threshold
ENABLE_LOW_LIGHT_BOOST=1

# Voting
STABLE_FRAMES=2         # Reads required before committing a plate
SAME_PLATE_COOLDOWN=8.0 # Seconds before same plate fires again

# Admin
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong password>
```

---

## 10. Deployment

### Development
```bash
# Backend
cd FYP/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd FYP/frontend
npm install
npm run dev
```

### Production
```bash
./start_system.sh
```

For production deployment with nginx + supervisord, see the configs in `deploy/`.

### Running Tests
```bash
cd FYP/backend
pip install pytest httpx
pytest tests/ -v
```

### Health Check
```
GET http://localhost:8000/api/health
→ { "status": "ok", "camera_worker": true, "database": true }
```

### Log Files
| File | Contents |
|------|----------|
| `FYP/logs/backend.log` | INFO+ application logs |
| `FYP/logs/uvicorn_errors.log` | Uvicorn error output |
| `FYP/logs/frontend.log` | Next.js output |
