# ANPR Frontend — Next.js Dashboard

React/Next.js 14 dashboard for the ANPR system. Connects to the FastAPI backend over HTTP and WebSocket.

## Stack

- Next.js 14 (Pages Router)
- TypeScript
- Tailwind CSS
- Sora + JetBrains Mono fonts

## Setup

```bash
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL
npm run dev
```

Dashboard runs at `http://localhost:3000`.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Redirects to `/login` |
| `/login` | Login page |
| `/dashboard` | Main dashboard |

## Dashboard Sections

- **Overview** — Live camera feed, stats cards, last detection
- **Detections** — Paginated detection log with CSV export
- **Vehicles** — Vehicle registry CRUD
- **Alerts** — Unauthorized detections only
