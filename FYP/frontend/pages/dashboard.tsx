import dynamic from "next/dynamic";
import Head from "next/head";
import { useRouter } from "next/router";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { clearAuth, getUser, isAuthed } from "../lib/auth";
import type {
  Detection,
  LiveDetection,
  Stats,
  User,
  Vehicle,
  VehicleForm,
} from "../lib/types";

import LastDetectionCard from "../components/LastDetectionCard";
import Sidebar from "../components/Sidebar";
import StatsCards from "../components/StatsCards";
import { isAdmin } from "../lib/auth";

const LiveFeed = dynamic(() => import("../components/LiveFeed"), {
  ssr: false,
  loading: () => (
    <div
      className="card rounded-xl flex flex-col items-center justify-center"
      style={{ aspectRatio: "16/9" }}
    >
      <div
        className="w-8 h-8 rounded-full border-2 animate-spin"
        style={{
          borderColor: "rgba(11,110,79,0.15)",
          borderTopColor: "var(--brand-mid)",
        }}
      />
      <p className="text-xs mt-3" style={{ color: "var(--text-3)" }}>
        Loading camera…
      </p>
    </div>
  ),
});

const DetectionTable = dynamic(() => import("../components/DetectionTable"), {
  ssr: false,
  loading: () => (
    <div className="card rounded-xl" style={{ height: "320px" }}>
      <div className="skeleton w-full h-full rounded-xl" />
    </div>
  ),
});

type Section = "overview" | "detections" | "vehicles" | "alerts";

const SUBTITLES: Record<Section, string> = {
  overview: "Live monitoring and system status",
  detections: "All detection events with filters",
  vehicles: "Vehicle registry management",
  alerts: "Unauthorized access events",
};

const SECTION_ICONS: Record<Section, React.ReactElement<unknown>> = {
  overview: (
    <svg
      className="w-4 h-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.75}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"
      />
    </svg>
  ),
  detections: (
    <svg
      className="w-4 h-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.75}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
      />
    </svg>
  ),
  vehicles: (
    <svg
      className="w-4 h-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.75}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9 17a2 2 0 11-4 0 2 2 0 014 0zM19 17a2 2 0 11-4 0 2 2 0 014 0zM13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10h10zM9 16h10a1 1 0 001-1v-3l-3.5-4H13v7z"
      />
    </svg>
  ),
  alerts: (
    <svg
      className="w-4 h-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.75}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
      />
    </svg>
  ),
};

/* Dashboard */
interface DashboardProps {
  toggleTheme?: () => void;
  theme?: "light" | "dark";
}

export default function Dashboard({ toggleTheme, theme }: DashboardProps) {
  const router = useRouter();

  const [ready, setReady] = useState(false);
  const [section, setSection] = useState<Section>("overview");
  const [stats, setStats] = useState<Stats | null>(null);
  const [lastDet, setLastDet] = useState<LiveDetection | null>(null);
  const [alerts, setAlerts] = useState<Detection[]>([]);
  const [alertTotal, setAlertTotal] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const [detectionCount, setDetectionCount] = useState(0);
  const [statsError, setStatsError] = useState<string | null>(null);

  const user: User | null = getUser();

  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    if (!isAuthed()) {
      router.replace("/login");
      return;
    }
    setReady(true);
    void loadStats();
    void loadAlerts();
    const iv = setInterval(() => {
      void loadStats();
      void loadAlerts();
    }, 15_000);
    return () => {
      mountedRef.current = false;
      clearInterval(iv);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadStats = useCallback(async () => {
    try {
      const data = await api.stats();
      if (!mountedRef.current) return;
      setStats(data);
      setStatsError(null);
    } catch (e) {
      if (!mountedRef.current) return;
      setStatsError(e instanceof Error ? e.message : "Failed to load stats");
    }
  }, []);

  const loadAlerts = useCallback(async () => {
    try {
      const r = await api.alerts({ per_page: 5 });
      if (!mountedRef.current) return;
      setAlerts(r?.items ?? []);
      setAlertTotal(r?.total ?? 0);
    } catch {
      // alerts are non-critical — fail silently
    }
  }, []);

  const lastRefreshRef = useRef<number>(0);

  const handleDetection = useCallback(
    (det: LiveDetection) => {
      setLastDet(det);
      setDetectionCount((n) => n + 1);
      // Refresh stats and alerts immediately on every new detection
      // Rate-limited to at most once every 2 seconds to avoid hammering the API
      const now = Date.now();
      if (now - lastRefreshRef.current > 2000) {
        lastRefreshRef.current = now;
        void loadStats();
        void loadAlerts();
      }
    },
    [loadStats, loadAlerts],
  );

  const handleLogout = () => {
    clearAuth();
    router.push("/login");
  };

  const isDark = theme === "dark";

  if (!ready) return null;

  return (
    <>
      <Head>
        <title>
          {alertTotal > 0 ? `(${alertTotal}) ` : ""}ANPR Dashboard
        </title>
      </Head>

      <div
        className="flex h-screen overflow-hidden"
        style={{ background: "var(--bg)" }}
      >
        {/* Sidebar */}
        <Sidebar
          active={section}
          onChange={setSection}
          alerts={alertTotal}
          onLogout={handleLogout}
          user={user}
          collapsed={collapsed}
          onToggle={() => setCollapsed((v) => !v)}
        />

        {/* Main content */}
        <main
          className="flex-1 flex flex-col overflow-hidden"
          style={{ minWidth: 0 }}
        >
          {/* Topbar */}
          <header
            className="flex items-center justify-between px-6 shrink-0"
            style={{
              background: "var(--surface)",
              borderBottom: "1px solid var(--border)",
              height: "64px",
              boxShadow: "var(--shadow-xs)",
            }}
          >
            <div className="flex items-center gap-3">
              <span style={{ color: "var(--text-3)" }}>
                {SECTION_ICONS[section]}
              </span>
              <div>
                <h1
                  className="font-bold capitalize leading-none tracking-tight"
                  style={{ fontSize: "0.9375rem", color: "var(--text)" }}
                >
                  {section}
                </h1>
                <p
                  className="text-xs mt-0.5 leading-none"
                  style={{ color: "var(--text-3)" }}
                >
                  {SUBTITLES[section]}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {/* Alert pill */}
              {alertTotal > 0 && (
                <button
                  onClick={() => setSection("alerts")}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-bold alert-pulse"
                  style={{
                    background: "var(--danger-bg)",
                    border: "1.5px solid var(--danger-border)",
                    color: "var(--danger-text)",
                    cursor: "pointer",
                  }}
                  aria-label={`${alertTotal} unauthorized alerts`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  {alertTotal} alert{alertTotal > 1 ? "s" : ""}
                </button>
              )}

              {/* Theme toggle */}
              {toggleTheme && (
                <button
                  onClick={toggleTheme}
                  className="btn-ghost w-9 h-9 flex items-center justify-center p-0 rounded-xl"
                  aria-label={
                    isDark ? "Switch to light mode" : "Switch to dark mode"
                  }
                  title={isDark ? "Light mode" : "Dark mode"}
                >
                  {isDark ? (
                    <svg
                      className="w-4 h-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
                      />
                    </svg>
                  ) : (
                    <svg
                      className="w-4 h-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
                      />
                    </svg>
                  )}
                </button>
              )}

              {/* User chip */}
              <div
                className="flex items-center gap-2 px-3 py-1.5 rounded-xl"
                style={{
                  background: "var(--surface-el)",
                  border: "1px solid var(--border)",
                }}
              >
                <div
                  className="w-5 h-5 rounded-full text-white text-xs font-bold flex items-center justify-center"
                  style={{
                    background:
                      "linear-gradient(135deg, var(--brand), var(--brand-mid))",
                  }}
                >
                  {user?.username?.[0]?.toUpperCase() ?? "A"}
                </div>
                <span
                  className="text-sm font-medium"
                  style={{ color: "var(--text-2)" }}
                >
                  {user?.username}
                </span>
              </div>
            </div>
          </header>

          {/* Page content */}
          <div className="flex-1 overflow-y-auto" style={{ padding: "24px" }}>
            {section === "overview" && (
              <div className="space-y-6 fade-up">
                {statsError && (
                  <div
                    className="px-4 py-3 rounded-xl text-sm font-medium"
                    style={{
                      background: "var(--danger-bg)",
                      border: "1.5px solid var(--danger-border)",
                      color: "var(--danger-text)",
                    }}
                  >
                    Stats unavailable: {statsError}
                  </div>
                )}
                <StatsCards stats={stats} />

                <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
                  <div className="xl:col-span-2">
                    <LiveFeed onDetection={handleDetection} />
                  </div>
                  <div>
                    <LastDetectionCard detection={lastDet} />
                  </div>
                </div>
              </div>
            )}

            {section === "detections" && (
              <div className="fade-up">
                <DetectionTable mode="all" refreshKey={detectionCount} />
              </div>
            )}

            {section === "alerts" && (
              <div className="fade-up">
                {/* Alert banner */}
                {alertTotal > 0 && (
                  <div
                    className="flex items-center gap-3 px-4 py-3 rounded-xl mb-4 text-sm font-medium"
                    style={{
                      background: "var(--danger-bg)",
                      border: "1.5px solid var(--danger-border)",
                      color: "var(--danger-text)",
                    }}
                  >
                    <svg
                      className="w-4 h-4 shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
                      />
                    </svg>
                    {alertTotal} unauthorized access event
                    {alertTotal > 1 ? "s" : ""} detected
                  </div>
                )}
                <DetectionTable mode="alerts" refreshKey={detectionCount} />
              </div>
            )}

            {section === "vehicles" && (
              <div className="fade-up">
                <VehicleTable />
              </div>
            )}
          </div>
        </main>
      </div>
    </>
  );
}

/* Vehicle Table (inline, colocated) */
const VEHICLE_EMPTY: VehicleForm = {
  vehicle_id_code: "",
  make: "",
  model: "",
  license_number: "",
  color: "",
  owner_name: "",
  owner_cnic: "",
  dues: "Clear",
  status: "Authorized",
  image_filename: "",
};

const FIELDS: Array<[keyof VehicleForm, string, number]> = [
  ["license_number", "License Plate *", 2],
  ["owner_name", "Owner Name", 1],
  ["make", "Make", 1],
  ["model", "Model", 1],
  ["color", "Colour", 1],
  ["owner_cnic", "CNIC", 1],
  ["vehicle_id_code", "Vehicle ID", 1],
  ["image_filename", "Image File", 1],
];

function VehicleTable() {
  const [rows, setRows] = useState<Vehicle[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<VehicleForm | null>(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const admin = isAdmin();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.vehicles({
        page,
        per_page: 20,
        search,
        ...(filter !== "" ? { is_authorized: Number(filter) } : {}),
      });
      setRows(r?.items ?? []);
      setTotal(r?.total ?? 0);
      setPages(r?.pages ?? 1);
    } catch (e) {
      setMsg("Error loading vehicles: " + (e instanceof Error ? e.message : "Unknown"));
    } finally {
      setLoading(false);
    }
  }, [page, search, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!form) return;
    try {
      if (editing === "new") {
        await api.createVehicle(form);
      } else if (typeof editing === "number") {
        await api.updateVehicle(editing, form);
      }
      setEditing(null);
      setForm(null);
      setMsg("Vehicle saved successfully");
      setTimeout(() => setMsg(""), 3000);
      void load();
    } catch (e) {
      setMsg("Error: " + (e instanceof Error ? e.message : "Unknown"));
    }
  };

  const del = async (id: number) => {
    if (!confirm("Delete this vehicle?")) return;
    try {
      await api.delVehicle(id);
      void load();
    } catch (e) {
      setMsg("Error: " + (e instanceof Error ? e.message : "Delete failed"));
    }
  };

  return (
    <div className="space-y-4 fade-up">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div className="flex gap-2.5">
          <div className="relative">
            <div
              className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
              style={{ color: "var(--text-4)" }}
            >
              <svg
                className="w-3.5 h-3.5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            </div>
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              placeholder="Search plate or owner…"
              className="input-base"
              style={{ width: "230px", paddingLeft: "2.25rem" }}
              aria-label="Search vehicles"
            />
          </div>
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(1);
            }}
            className="input-base"
            style={{ width: "155px" }}
            aria-label="Filter by authorization"
          >
            <option value="">All vehicles</option>
            <option value="1">Authorized</option>
            <option value="0">Unauthorized</option>
          </select>
        </div>
        <button
          onClick={() => {
            setEditing("new");
            setForm({ ...VEHICLE_EMPTY });
          }}
          className="btn-primary px-4 py-2 flex items-center gap-2"
          style={{ display: admin ? undefined : "none" }}
        >
          <svg
            className="w-4 h-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 4v16m8-8H4"
            />
          </svg>
          Add Vehicle
        </button>
      </div>

      {/* Status message */}
      {msg && (
        <div
          className="px-4 py-3 rounded-xl text-sm font-medium flex items-center gap-2"
          style={{
            background: msg.startsWith("Error")
              ? "var(--danger-bg)"
              : "var(--success-bg)",
            border: msg.startsWith("Error")
              ? "1.5px solid var(--danger-border)"
              : "1.5px solid var(--success-border)",
            color: msg.startsWith("Error")
              ? "var(--danger-text)"
              : "var(--success-text)",
          }}
        >
          {msg}
        </div>
      )}

      {/* Table */}
      <div className="card rounded-xl overflow-hidden">
        <div
          className="overflow-x-auto overflow-y-auto"
          style={{ maxHeight: "520px" }}
        >
          <table className="data-table">
            <thead>
              <tr>
                {[
                  "Plate",
                  "Owner",
                  "Make / Model",
                  "Colour",
                  "Dues",
                  "Status",
                  "Auth",
                  "Actions",
                ].map((h) => (
                  <th key={h} scope="col">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i}>
                    {Array.from({ length: 8 }).map((_, j) => (
                      <td key={j}>
                        <div className="skeleton h-3 w-20 rounded" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-16 text-center">
                    <div className="flex flex-col items-center gap-3">
                      <div
                        className="w-12 h-12 rounded-xl flex items-center justify-center"
                        style={{
                          background: "var(--surface-el)",
                          border: "1px solid var(--border)",
                        }}
                      >
                        <svg
                          className="w-5 h-5"
                          style={{ color: "var(--text-4)" }}
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M9 17a2 2 0 11-4 0 2 2 0 014 0zM19 17a2 2 0 11-4 0 2 2 0 014 0zM13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10h10zM9 16h10a1 1 0 001-1v-3l-3.5-4H13v7z"
                          />
                        </svg>
                      </div>
                      <p className="text-sm" style={{ color: "var(--text-3)" }}>
                        No vehicles found
                      </p>
                    </div>
                  </td>
                </tr>
              ) : (
                rows.map((v) => (
                  <tr key={v.id}>
                    <td>
                      <span
                        className="plate-display text-sm font-bold"
                        style={{ color: "var(--brand-mid)" }}
                      >
                        {v.license_normalized}
                      </span>
                    </td>
                    <td style={{ color: "var(--text)" }}>
                      {v.owner_name || "—"}
                    </td>
                    <td style={{ color: "var(--text-2)" }}>
                      {[v.make, v.model].filter(Boolean).join(" ") || "—"}
                    </td>
                    <td style={{ color: "var(--text-2)" }}>{v.color || "—"}</td>
                    <td>
                      <span
                        className={`badge ${v.dues?.toLowerCase() === "clear" ? "badge-auth" : "badge-warn"}`}
                      >
                        {v.dues || "N/A"}
                      </span>
                    </td>
                    <td className="text-xs" style={{ color: "var(--text-2)" }}>
                      {v.status}
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <span
                          className="w-2 h-2 rounded-full"
                          style={{
                            background: v.is_authorized
                              ? "var(--success)"
                              : "var(--danger)",
                            boxShadow: v.is_authorized
                              ? "0 0 0 3px var(--success-bg)"
                              : "0 0 0 3px var(--danger-bg)",
                          }}
                        />
                        <span
                          className="text-xs font-medium"
                          style={{
                            color: v.is_authorized
                              ? "var(--success-text)"
                              : "var(--danger-text)",
                          }}
                        >
                          {v.is_authorized ? "Yes" : "No"}
                        </span>
                      </div>
                    </td>
                    <td>
                      {admin && (
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            setEditing(v.id);
                            setForm({ ...v } as VehicleForm);
                          }}
                          className="text-xs font-semibold px-2.5 py-1 rounded-lg transition-colors"
                          style={{
                            color: "var(--brand-mid)",
                            background: "var(--brand-pale)",
                            border: "1px solid var(--brand-muted)",
                          }}
                          aria-label={`Edit vehicle ${v.license_normalized}`}
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => void del(v.id)}
                          className="text-xs font-medium px-2 py-1 rounded-lg transition-colors"
                          style={{
                            color: "var(--text-3)",
                            background: "transparent",
                            border: "1px solid var(--border)",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.color = "var(--danger-text)";
                            e.currentTarget.style.background =
                              "var(--danger-bg)";
                            e.currentTarget.style.borderColor =
                              "var(--danger-border)";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.color = "var(--text-3)";
                            e.currentTarget.style.background = "transparent";
                            e.currentTarget.style.borderColor = "var(--border)";
                          }}
                          aria-label={`Delete vehicle ${v.license_normalized}`}
                        >
                          Del
                        </button>
                      </div>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs" style={{ color: "var(--text-3)" }}>
            Page {page} of {pages} &mdash; {total} vehicle{total !== 1 ? "s" : ""}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="btn-ghost px-3 py-1.5 text-xs font-semibold rounded-lg"
              style={{ opacity: page <= 1 ? 0.4 : 1 }}
              aria-label="Previous page"
            >
              ← Prev
            </button>
            {Array.from({ length: Math.min(pages, 7) }, (_, i) => {
              const p = pages <= 7 ? i + 1 : page <= 4 ? i + 1 : page + i - 3;
              if (p < 1 || p > pages) return null;
              return (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className="w-8 h-8 rounded-lg text-xs font-semibold"
                  style={{
                    background: p === page ? "var(--brand)" : "transparent",
                    color: p === page ? "#fff" : "var(--text-2)",
                    border: p === page ? "none" : "1px solid var(--border)",
                  }}
                  aria-label={`Page ${p}`}
                  aria-current={p === page ? "page" : undefined}
                >
                  {p}
                </button>
              );
            })}
            <button
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
              disabled={page >= pages}
              className="btn-ghost px-3 py-1.5 text-xs font-semibold rounded-lg"
              style={{ opacity: page >= pages ? 0.4 : 1 }}
              aria-label="Next page"
            >
              Next →
            </button>
          </div>
        </div>
      )}

      {/* Modal */}
      {admin && editing !== null && form !== null && (
        <div
          className="overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              setEditing(null);
              setForm(null);
            }
          }}
          role="dialog"
          aria-modal="true"
          aria-label={editing === "new" ? "Add vehicle" : "Edit vehicle"}
        >
          <div
            className="card rounded-2xl p-6 w-full mx-4 scale-in"
            style={{
              maxWidth: "520px",
              background: "var(--surface)",
              borderColor: "var(--border-hi)",
              boxShadow: "var(--shadow-xl)",
            }}
          >
            <div className="flex items-center justify-between mb-6">
              <div>
                <h3
                  className="font-bold tracking-tight"
                  style={{ fontSize: "1.0625rem", color: "var(--text)" }}
                >
                  {editing === "new" ? "Add Vehicle" : "Edit Vehicle"}
                </h3>
                <p
                  className="text-xs mt-0.5"
                  style={{ color: "var(--text-3)" }}
                >
                  {editing === "new"
                    ? "Register a new vehicle in the system"
                    : "Update vehicle information"}
                </p>
              </div>
              <button
                onClick={() => {
                  setEditing(null);
                  setForm(null);
                }}
                className="w-8 h-8 rounded-xl flex items-center justify-center btn-ghost p-0"
                aria-label="Close modal"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              {FIELDS.map(([key, label, cols]) => (
                <div key={key} style={{ gridColumn: `span ${cols}` }}>
                  <label
                    className="block text-xs font-semibold mb-1.5"
                    style={{ color: "var(--text-2)" }}
                    htmlFor={`field-${key}`}
                  >
                    {label}
                  </label>
                  <input
                    id={`field-${key}`}
                    value={(form[key] as string) ?? ""}
                    onChange={(e) =>
                      setForm((f) => (f ? { ...f, [key]: e.target.value } : f))
                    }
                    className="input-base"
                  />
                </div>
              ))}

              <div>
                <label
                  className="block text-xs font-semibold mb-1.5"
                  style={{ color: "var(--text-2)" }}
                >
                  Dues
                </label>
                <select
                  value={form.dues}
                  onChange={(e) =>
                    setForm((f) =>
                      f
                        ? { ...f, dues: e.target.value as VehicleForm["dues"] }
                        : f,
                    )
                  }
                  className="input-base"
                >
                  <option>Clear</option>
                  <option>Paid</option>
                  <option>Remaining</option>
                </select>
              </div>

              <div>
                <label
                  className="block text-xs font-semibold mb-1.5"
                  style={{ color: "var(--text-2)" }}
                >
                  Status
                </label>
                <select
                  value={form.status}
                  onChange={(e) =>
                    setForm((f) => (f ? { ...f, status: e.target.value } : f))
                  }
                  className="input-base"
                >
                  <option>Authorized</option>
                  <option>Unauthorized</option>
                </select>
              </div>
            </div>

            <div
              className="flex items-center justify-end gap-3 mt-6 pt-5"
              style={{ borderTop: "1px solid var(--border)" }}
            >
              <button
                onClick={() => {
                  setEditing(null);
                  setForm(null);
                }}
                className="btn-ghost px-5 py-2.5"
              >
                Cancel
              </button>
              <button
                onClick={() => void save()}
                className="btn-primary px-5 py-2.5"
              >
                Save vehicle
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
