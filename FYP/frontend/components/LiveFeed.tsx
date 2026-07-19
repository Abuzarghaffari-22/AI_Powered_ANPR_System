import { memo, useEffect, useRef, useState } from "react";
import type { LiveDetection } from "../lib/types";
import { getToken } from "../lib/auth";

let WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";

if (typeof window !== "undefined") {
  const host = window.location.hostname;
  if (WS_BASE.includes("localhost") && host !== "localhost" && host !== "127.0.0.1") {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    WS_BASE = `${protocol}//${host}:8000`;
  }
}

interface Props {
  onDetection?: (d: LiveDetection) => void;
}

function LiveFeed({ onDetection }: Props) {
  const imgRef        = useRef<HTMLImageElement>(null);
  const wsRef         = useRef<WebSocket | null>(null);
  const retryTimer    = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimer     = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef    = useRef(true);
  const onDetRef      = useRef(onDetection);
  onDetRef.current    = onDetection;

  const fpsRef        = useRef({ count: 0, ts: Date.now() });
  const lastPlateRef  = useRef<string>("");
  const plateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasFrameRef   = useRef(false);   // true once the first JPEG arrives
  const [connected,    setConnected]    = useState(false);
  const [hasFrame,     setHasFrame]     = useState(false);
  const [frameCount,   setFrameCount]   = useState(0);
  const [fps,          setFps]          = useState(0);
  const [cameraStatus, setCameraStatus] = useState<string>("Connecting to camera…");
  const [lastStatus,   setLastStatus]   = useState<"authorized" | "unauthorized" | "unknown" | null>(null);

  useEffect(() => {
    mountedRef.current = true;

    function clearTimers() {
      if (retryTimer.current)   { clearTimeout(retryTimer.current);   retryTimer.current   = null; }
      if (pingTimer.current)    { clearInterval(pingTimer.current);   pingTimer.current    = null; }
      if (plateTimerRef.current){ clearTimeout(plateTimerRef.current);plateTimerRef.current = null; }
    }

    async function connect() {
      if (!mountedRef.current) return;

      clearTimers();

      // Close any existing socket cleanly without triggering our onclose retry
      const old = wsRef.current;
      if (old) {
        old.onopen    = null;
        old.onmessage = null;
        old.onerror   = null;
        old.onclose   = null;
        if (old.readyState === WebSocket.OPEN || old.readyState === WebSocket.CONNECTING) {
          old.close();
        }
        wsRef.current = null;
      }

      // Exchange JWT for a short-lived ticket so the token never appears in the WS URL
      let wsUrl = `${WS_BASE}/api/stream`;
      try {
        const apiBase = WS_BASE.replace(/^ws/, "http");
        const token = getToken() ?? "";
        const res = await fetch(`${apiBase}/api/stream/ticket`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const { ticket } = await res.json() as { ticket: string };
          wsUrl = `${WS_BASE}/api/stream?ticket=${encodeURIComponent(ticket)}`;
        } else {
          // Ticket endpoint failed — close without connecting, retry will re-attempt
          retryTimer.current = setTimeout(connect, 3_000);
          return;
        }
      } catch {
        // Network error fetching ticket — retry after delay
        retryTimer.current = setTimeout(connect, 3_000);
        return;
      }

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) { ws.close(); return; }
        setConnected(true);
        setCameraStatus("Camera connected — waiting for first frame…");
        // Keep-alive ping every 20 s
        pingTimer.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 20_000);
      };

      ws.onmessage = (ev: MessageEvent<string>) => {
        if (!mountedRef.current) return;
        try {
          const data = JSON.parse(ev.data) as {
            type: string;
            frame?: string;
            frame_num?: number;
            ts?: string;
            is_new?: boolean;
            detection?: {
              plate: string;
              status: "authorized" | "unauthorized" | "unknown";
              yolo_conf: number;
              ocr_conf: number;
              match_type: string;
              vehicle?: LiveDetection["vehicle"];
            } | null;
            message?: string;
          };

          if (data.type === "camera_status") {
            if (data.message) setCameraStatus(data.message);
            return;
          }

          if (data.type !== "frame" || !data.frame) return;

          // Update video frame — show feed immediately on first frame
          if (imgRef.current) {
            imgRef.current.src = `data:image/jpeg;base64,${data.frame}`;
          }
          // Reveal feed on very first frame (removes the spinner overlay)
          if (!hasFrameRef.current) {
            hasFrameRef.current = true;
            setHasFrame(true);
          }
          if (data.frame_num !== undefined) setFrameCount(data.frame_num);

          // FPS counter
          fpsRef.current.count++;
          const elapsed = Date.now() - fpsRef.current.ts;
          if (elapsed >= 1000) {
            setFps(Math.round((fpsRef.current.count * 1000) / elapsed));
            fpsRef.current = { count: 0, ts: Date.now() };
          }

          // Always update the overlay status when detection is present
          if (data.detection) {
            setLastStatus(data.detection.status);
          } else {
            // No detection in this frame — keep overlay for 3s then clear
            if (!lastPlateRef.current) {
              setLastStatus(null);
            }
          }

          // Fire onDetection callback ONLY for genuinely new plate+status events
          // Backend sets is_new=true when plate/status key changes (not every frame)
          if (data.detection && data.is_new && data.ts && onDetRef.current) {
            const det: LiveDetection = { ...data.detection, ts: data.ts };
            const key = det.plate + "|" + det.status;
            // Client-side dedup as a safety net (backend already enforces cooldown)
            if (key !== lastPlateRef.current) {
              lastPlateRef.current = key;
              onDetRef.current(det);
              // Clear client dedup key after backend cooldown window (8s)
              if (plateTimerRef.current) clearTimeout(plateTimerRef.current);
              plateTimerRef.current = setTimeout(() => {
                lastPlateRef.current = "";
              }, 8_000);
            }
          }
        } catch {
          /* ignore JSON parse errors */
        }
      };

      ws.onerror = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        setCameraStatus("Connection error — retrying…");
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        clearTimers();
        setConnected(false);
        hasFrameRef.current = false;
        setHasFrame(false);
        setCameraStatus("Disconnected — reconnecting…");
        // Reconnect after 1.5 s (was 3 s)
        retryTimer.current = setTimeout(connect, 1_500);
      };
    }

    connect();

    return () => {
      mountedRef.current  = false;
      hasFrameRef.current = false;
      clearTimers();
      const ws = wsRef.current;
      if (ws) {
        ws.onopen    = null;
        ws.onmessage = null;
        ws.onerror   = null;
        ws.onclose   = null;
        ws.close();
        wsRef.current = null;
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const flashColor =
    lastStatus === "authorized"   ? "var(--success)" :
    lastStatus === "unauthorized" ? "var(--danger)"  : "transparent";

  return (
    <div className="card rounded-xl overflow-hidden h-full flex flex-col">
      {/* Header */}
      <div
        className="flex items-center justify-between px-5 py-3.5 shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center">
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{ background: connected ? "var(--success)" : "var(--danger)" }}
            />
            {connected && (
              <span
                className="absolute w-2.5 h-2.5 rounded-full animate-ping"
                style={{ background: "var(--success)", opacity: 0.4 }}
              />
            )}
          </div>
          <span className="text-sm font-semibold" style={{ color: "var(--text)" }}>
            Live Camera Feed
          </span>
        </div>

        <div className="flex items-center gap-4">
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold"
            style={{ background: "var(--surface-el)", border: "1px solid var(--border)", color: "var(--text-2)" }}
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            <span className="tabular-nums">{fps} fps</span>
          </div>

          <span className="text-xs tabular-nums font-medium" style={{ color: "var(--text-3)" }}>
            #{frameCount.toLocaleString()}
          </span>

          <span
            className="text-xs font-semibold"
            style={{ color: connected ? "var(--success-text)" : "var(--danger-text)" }}
          >
            {connected ? "Connected" : "Reconnecting…"}
          </span>
        </div>
      </div>

      {/* Video area */}
      <div
        className="relative flex-1 overflow-hidden"
        style={{ background: "#0A0A0A", aspectRatio: "16/9" }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          ref={imgRef}
          alt="Live ANPR camera feed"
          className="w-full h-full object-contain"
          loading="eager"
          decoding="async"
        />

        {/* Detection flash border */}
        {lastStatus && (
          <div
            className="absolute inset-0 pointer-events-none transition-all duration-300"
            style={{ boxShadow: `inset 0 0 0 3px ${flashColor}`, opacity: 0.7 }}
          />
        )}

        {/* Connecting overlay — shown until the first JPEG frame arrives */}
        {!hasFrame && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-4"
            style={{ background: "rgba(0,0,0,0.80)" }}
          >
            <div
              className="w-10 h-10 rounded-full border-2 animate-spin"
              style={{ borderColor: "rgba(11,110,79,0.2)", borderTopColor: "var(--brand-mid)" }}
            />
            <div className="text-center px-6">
              <p className="text-sm font-semibold text-white/80">{cameraStatus}</p>
              <p className="text-xs text-white/40 mt-1">
                {connected ? "Feed loading…" : "Retrying in a moment…"}
              </p>
            </div>
          </div>
        )}

        {/* LIVE badge */}
        {hasFrame && (
          <div
            className="absolute top-3 left-3 flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-bold"
            style={{
              background: "rgba(0,0,0,0.65)",
              backdropFilter: "blur(8px)",
              color: "#ff4d4d",
              border: "1px solid rgba(255,77,77,0.3)",
            }}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" />
            LIVE
          </div>
        )}

        {/* Corner scan-line brackets */}
        {hasFrame && (
          <div className="absolute inset-4 pointer-events-none opacity-20">
            <span className="absolute top-0 left-0 w-6 h-6 border-t-2 border-l-2 rounded-tl"  style={{ borderColor: "var(--brand-light)" }} />
            <span className="absolute top-0 right-0 w-6 h-6 border-t-2 border-r-2 rounded-tr" style={{ borderColor: "var(--brand-light)" }} />
            <span className="absolute bottom-0 left-0 w-6 h-6 border-b-2 border-l-2 rounded-bl" style={{ borderColor: "var(--brand-light)" }} />
            <span className="absolute bottom-0 right-0 w-6 h-6 border-b-2 border-r-2 rounded-br" style={{ borderColor: "var(--brand-light)" }} />
          </div>
        )}

        {/* Last detection status badge */}
        {lastStatus && connected && (
          <div
            className="absolute bottom-3 right-3 flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-bold"
            style={{
              background: "rgba(0,0,0,0.70)",
              backdropFilter: "blur(8px)",
              color: lastStatus === "authorized" ? "#4ade80" : "#f87171",
              border: `1px solid ${lastStatus === "authorized" ? "rgba(74,222,128,0.3)" : "rgba(248,113,113,0.3)"}`,
            }}
          >
            {lastStatus === "authorized" ? "✓ AUTHORIZED" : "✗ UNAUTHORIZED"}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(LiveFeed);
