<img width="1844" height="922" alt="Screenshot from 2026-07-09 00-24-49" src="https://github.com/user-attachments/assets/615e5df0-f9cb-4d39-9aea-73dde139363f" />


### Login
Secure JWT-based authentication gating access to the dashboard.

<img width="1844" height="922" alt="Screenshot from 2026-07-09 00-28-36" src="https://github.com/user-attachments/assets/6d03d16d-42c6-4895-88d2-924e7b7142e5" />

### Dashboard Overview
Live system stats (total vehicles, authorized count, today's detections) with a real-time camera feed streamed over WebSocket from a DroidCam IP source.

<img width="1844" height="922" alt="Screenshot from 2026-07-09 00-06-47" src="https://github.com/user-attachments/assets/361e9d8f-962a-433e-a85e-546490564687" />

### Detection — Authorized Vehicle
YOLOv11 localizes the plate (73% conf.) and Tesseract OCR reads it (95% conf.). The result is matched against the MySQL registry — owner, vehicle, dues status — and access is granted in real time

<img width="1844" height="922" alt="Screenshot from 2026-07-09 00-07-31" src="https://github.com/user-attachments/assets/382795f4-b428-4704-98a9-efda68add36c" />

### Detection — Unauthorized Vehicle
Same pipeline flags a plate not cleared in the database — vehicle info is still resolved via fuzzy matching, but status is marked unauthorized and logged to Alerts.

# ANPR System — Automatic Number Plate Recognition

Final Year Project | BSCS | AI-powered license plate detection and vehicle authorization system built for Pakistani road conditions.

## Overview

The system uses a custom-trained YOLOv11 model to detect license plates from a live camera feed, Tesseract OCR to read the plate text, and a MySQL database to check whether the vehicle is authorized. Results are streamed in real time to a Next.js web dashboard over WebSocket.

**Stack:** FastAPI · Next.js 14 · YOLOv11 · Tesseract OCR · MySQL · TailwindCSS

## Features

- Real-time plate detection and OCR from webcam or IP camera (DroidCam)
- Multi-variant OCR voting pipeline for improved accuracy on Pakistani plates
- In-memory plate store with fuzzy matching (edit distance, OCR confusion correction)
- JWT-authenticated REST API and WebSocket stream
- Admin dashboard: live feed, detection logs, vehicle registry, alerts
- Dark/light theme, collapsible sidebar, animated stats cards

## Project Structure

```
ANPR_Project/
├── FYP/
│   ├── backend/          # FastAPI + AI pipeline
│   │   ├── main.py
│   │   ├── pipeline.py
│   │   ├── plate_store.py
│   │   ├── camera_worker.py
│   │   ├── auth.py
│   │   ├── database.py
│   │   ├── schemas.py
│   │   ├── routes/
│   │   ├── models/       # YOLO weights (not in repo — see below)
│   │   └── tests/
│   ├── frontend/         # Next.js dashboard
│   │   ├── pages/
│   │   ├── components/
│   │   └── lib/
│   └── data/
│       ├── Car_info.xlsx
│       └── car_data/
├── deploy/               # nginx + supervisor configs
└── start_system.sh
```

## Setup

### Requirements

- Python 3.10+
- Node.js 18+
- MySQL 8.0+
- Tesseract OCR 5.x (`sudo apt install tesseract-ocr`)

### Backend

```bash
cd FYP/backend
pip install -r requirements.txt
cp .env.example .env        # fill in your values
mysql -u root -p < database_schema.sql
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd FYP/frontend
npm install
npm run dev
```

Dashboard runs at `http://localhost:3000`. API docs at `http://localhost:8000/api/docs`.

### YOLO Model

The model file (`yolov11_plate_detection.pt`) is not included in this repository due to file size. Place it at:

```
FYP/backend/models/yolov11_plate_detection.pt
```

## Configuration

Copy `FYP/backend/.env.example` to `.env` and set:

Variable | Description 

`DB_HOST / DB_USER / DB_PASSWORD / DB_NAME` | MySQL connection |
`SECRET_KEY` | Random 32+ char string for JWT signing |
`ADMIN_USERNAME / ADMIN_PASSWORD` | Initial admin account (12+ chars, mixed) |
`CAMERA_INDEX` | Webcam device index (default 0) |
`CAMERA_IP` | IP camera address — leave blank for local webcam |
`CONF_THRESHOLD` | YOLO confidence threshold (default 0.45) |
`MIN_OCR_CONF` | Minimum OCR score to accept a plate (default 0.75) |

## Running Tests

```bash
cd FYP/backend
python -m pytest tests/
```

## Access
`ws://localhost:8000/api/stream` | Live WebSocket stream |

URL | Purpose 

`http://localhost:3000` | Dashboard |
`http://localhost:8000/api/docs` | API documentation |
`http://localhost:8000/api/health` | Health check |

## License

This project was developed as a Final Year Project for academic purposes.# AI_Powered_ANPR_System
