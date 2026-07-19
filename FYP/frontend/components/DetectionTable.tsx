import { memo, useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { isAdmin } from "../lib/auth";
import type {
  Detection,
  DetectionFilters,
  DetectionStatus,
} from "../lib/types";

/* Status badge */

function StatusBadge({ status }: { status: DetectionStatus | string }) {
  const cls =
    status === "authorized"
      ? "badge badge-auth"
      : status === "unauthorized"
        ? "badge badge-unauth"
        : "badge badge-unknown";
  return <span className={cls}>{status}</span>;
}

/* Timestamp */
function fmt(ts: string | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString([], {
      dateStyle: "short",
      timeStyle: "medium",
    });
  } catch {
    return ts;
  }
}

/* Skeleton row */

function SkeletonRows({ cols = 8 }: { cols?: number }) {
  return (
    <>
      {Array.from({ length: 6 }).map((_, i) => (
        <tr key={i}>
          {Array.from({ length: cols }).map((_, j) => (
            <td key={j} className="px-4 py-3.5">
              <div
                className="skeleton h-3 rounded"
                style={{ width: `${40 + ((i * j * 7) % 45)}%` }}
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/* Confidence bar */

function ConfBar({ val }: { val: number | undefined }) {
  if (val === null || val === undefined)
    return <span style={{ color: "var(--text-4)" }}>—</span>;

  const pct = Math.round(val * 100);
  const color =
    pct >= 80 ? "var(--success)" : pct >= 50 ? "var(--warn)" : "var(--danger)";

  return (
    <div className="flex items-center gap-2">
      <div
        className="w-16 h-1.5 rounded-full overflow-hidden"
        style={{ background: "var(--surface-hi)" }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="text-xs tabular-nums font-semibold" style={{ color }}>
        {pct}%
      </span>
    </div>
  );
}

/* Filter bar */

interface FilterBarProps {
  filters: DetectionFilters;
  onChange: (k: keyof DetectionFilters, v: string) => void;
  onClear: () => void;
}

const FilterBar = memo(({ filters, onChange, onClear }: FilterBarProps) => {
  const hasFilters = Object.values(filters).some((v) => v && v !== "");

  return (
    <div className="flex flex-wrap items-center gap-2.5">
      {/* Search */}
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
          value={filters.plate ?? ""}
          onChange={(e) => onChange("plate", e.target.value)}
          placeholder="Search plate…"
          className="input-base"
          style={{ width: "175px", paddingLeft: "2.25rem" }}
          aria-label="Search by plate"
        />
      </div>

      {/* Status */}
      <select
        value={filters.status ?? ""}
        onChange={(e) => onChange("status", e.target.value)}
        className="input-base"
        style={{ width: "155px" }}
        aria-label="Filter by status"
      >
        <option value="">All statuses</option>
        <option value="authorized">Authorized</option>
        <option value="unauthorized">Unauthorized</option>
      </select>

      {/* Date range */}
      <input
        type="date"
        value={filters.date_from ?? ""}
        onChange={(e) => onChange("date_from", e.target.value)}
        className="input-base"
        style={{ width: "150px" }}
        aria-label="From date"
      />
      <input
        type="date"
        value={filters.date_to ?? ""}
        onChange={(e) => onChange("date_to", e.target.value)}
        className="input-base"
        style={{ width: "150px" }}
        aria-label="To date"
      />

      {/* Clear */}
      {hasFilters && (
        <button
          onClick={onClear}
          className="btn-ghost text-xs px-3 py-2 flex items-center gap-1.5"
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
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
          Clear
        </button>
      )}
    </div>
  );
});
FilterBar.displayName = "FilterBar";

/* Main component */
interface Props {
  mode?: "all" | "alerts";
  refreshKey?: number;
}

function DetectionTable({ mode = "all", refreshKey }: Props) {
  const [rows, setRows] = useState<Detection[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const admin = isAdmin();

  const [filters, setFilters] = useState<DetectionFilters>({
    plate: "",
    status: "",
    date_from: "",
    date_to: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const params =
        mode === "alerts"
          ? { page, per_page: 20 }
          : { page, per_page: 20, ...filters };
      const fn = mode === "alerts" ? api.alerts : api.detections;
      const r = await fn(params);
      setRows(r?.items ?? []);
      setTotal(r?.total ?? 0);
      setPages(r?.pages ?? 1);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [mode, page, filters]);

  useEffect(() => {
    void load();
  }, [load]);

  // Reload immediately when parent signals a new detection (works even if key
  // increments while this tab is active — no missed updates)

  useEffect(() => {
    if (refreshKey !== undefined && refreshKey > 0) void load();
  }, [refreshKey, load]);

  // Auto-poll every 5 s so table stays live without relying on refreshKey
  useEffect(() => {
    const iv = setInterval(() => void load(), 5_000);
    return () => clearInterval(iv);
  }, [load]);

  const handleExport = async () => {
    setExporting(true);
    try {
      await api.exportDetections(mode === "alerts" ? { status: "unauthorized" } : filters);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const del = async (id: number) => {
    if (!confirm("Delete this detection log?")) return;
    try {
      await api.delDetect(id);
      void load();
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const handleFilter = (k: keyof DetectionFilters, v: string) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setPage(1);
  };

  const clearFilters = () => {
    setFilters({ plate: "", status: "", date_from: "", date_to: "" });
    setPage(1);
  };

  return (
    <div className="space-y-4 fade-up">
      {/* Filter bar */}
      {mode !== "alerts" && (
        <FilterBar
          filters={filters}
          onChange={handleFilter}
          onClear={clearFilters}
        />
      )}

      {/* Table card */}
      <div className="card rounded-xl overflow-hidden">
        {/* Table header */}
        <div
          className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          <div className="flex items-center gap-3">
            {mode === "alerts" && (
              <div
                className="w-7 h-7 rounded-lg flex items-center justify-center"
                style={{
                  background: "var(--danger-bg)",
                  border: "1px solid var(--danger-border)",
                }}
              >
                <svg
                  className="w-3.5 h-3.5"
                  style={{ color: "var(--danger)" }}
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
              </div>
            )}
            <span
              className="text-sm font-semibold"
              style={{ color: "var(--text)" }}
            >
              {mode === "alerts" ? "Unauthorized Alerts" : "Detection Logs"}
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span
              className="text-xs font-medium tabular-nums px-2.5 py-1 rounded-full"
              style={{
                background: "var(--surface-el)",
                border: "1px solid var(--border)",
                color: "var(--text-2)",
              }}
            >
              {total.toLocaleString()} records
            </span>

            {/* Export CSV button — admin only */}
            {admin && mode !== "alerts" && (
              <button
                onClick={() => void handleExport()}
                className="btn-ghost text-xs px-2.5 py-1.5 flex items-center gap-1.5"
                disabled={exporting}
                aria-label="Export CSV"
              >
                <svg
                  className="w-3.5 h-3.5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                  />
                </svg>
                {exporting ? "Exporting…" : "Export CSV"}
              </button>
            )}

            {/* Refresh button */}
            <button
              onClick={() => void load()}
              className="btn-ghost text-xs px-2.5 py-1.5 flex items-center gap-1.5"
              disabled={loading}
              aria-label="Refresh"
            >
              <svg
                className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              Refresh
            </button>
          </div>
        </div>

        {/* Scrollable table wrapper */}
        <div
          className="overflow-x-auto overflow-y-auto"
          style={{ maxHeight: "480px" }}
        >
          {loadError && (
            <div
              className="px-5 py-3 text-sm font-medium"
              style={{ color: "var(--danger-text)", background: "var(--danger-bg)" }}
            >
              {loadError}
            </div>
          )}
          <table className="data-table">
            <thead>
              <tr>
                {[
                  "#",
                  "Detected Plate",
                  "Matched Plate",
                  "Owner",
                  "Status",
                  "Confidence",
                  "Timestamp",
                  "",
                ].map((h) => (
                  <th key={h} scope="col">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows cols={8} />
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-20 text-center">
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
                            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                          />
                        </svg>
                      </div>
                      <p className="text-sm" style={{ color: "var(--text-3)" }}>
                        No records found
                      </p>
                    </div>
                  </td>
                </tr>
              ) : (
                rows.map((r) => (
                  <tr
                    key={r.id}
                    className={r.status === "unauthorized" ? "row-unauth" : ""}
                  >
                    <td>
                      <span
                        className="text-xs tabular-nums font-medium"
                        style={{ color: "var(--text-4)" }}
                      >
                        {r.id}
                      </span>
                    </td>
                    <td>
                      <span
                        className="plate-display text-sm font-bold"
                        style={{ color: "var(--brand-mid)" }}
                      >
                        {r.detected_plate || "—"}
                      </span>
                    </td>
                    <td>
                      <span
                        className="plate-display text-xs"
                        style={{ color: "var(--text-2)" }}
                      >
                        {r.matched_plate || "—"}
                      </span>
                    </td>
                    <td>
                      <span
                        className="text-sm"
                        style={{ color: "var(--text)" }}
                      >
                        {r.owner_name || "—"}
                      </span>
                    </td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td>
                      <ConfBar val={r.confidence} />
                    </td>
                    <td>
                      <span
                        className="text-xs tabular-nums whitespace-nowrap"
                        style={{ color: "var(--text-3)" }}
                      >
                        {fmt(r.detected_at)}
                      </span>
                    </td>
                    <td>
                      {admin && (
                      <button
                        onClick={() => void del(r.id)}
                        className="btn-ghost text-xs px-2.5 py-1 flex items-center gap-1"
                        style={{ color: "var(--text-3)" }}
                        onMouseEnter={(e) =>
                          (e.currentTarget.style.color = "var(--danger-text)")
                        }
                        onMouseLeave={(e) =>
                          (e.currentTarget.style.color = "var(--text-3)")
                        }
                        aria-label={`Delete detection ${r.id}`}
                      >
                        <svg
                          className="w-3 h-3"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                          strokeWidth={2}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                          />
                        </svg>
                        Del
                      </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {pages > 1 && (
          <div
            className="flex items-center justify-between px-5 py-3.5"
            style={{ borderTop: "1px solid var(--border)" }}
          >
            <span
              className="text-xs tabular-nums"
              style={{ color: "var(--text-3)" }}
            >
              Page {page} of {pages} · {total.toLocaleString()} total
            </span>

            <div className="flex items-center gap-1.5">
              <button
                disabled={page === 1}
                onClick={() => setPage(1)}
                className="btn-ghost text-xs px-2.5 py-1.5 disabled:opacity-30"
                aria-label="First page"
              >
                «
              </button>
              <button
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
                className="btn-ghost text-xs px-3 py-1.5 disabled:opacity-30"
                aria-label="Previous page"
              >
                ← Prev
              </button>
              <span
                className="px-3 py-1.5 text-xs font-semibold rounded-lg"
                style={{
                  background: "var(--brand-pale)",
                  color: "var(--brand)",
                  border: "1px solid var(--brand-muted)",
                }}
              >
                {page}
              </span>
              <button
                disabled={page === pages}
                onClick={() => setPage((p) => p + 1)}
                className="btn-ghost text-xs px-3 py-1.5 disabled:opacity-30"
                aria-label="Next page"
              >
                Next →
              </button>
              <button
                disabled={page === pages}
                onClick={() => setPage(pages)}
                className="btn-ghost text-xs px-2.5 py-1.5 disabled:opacity-30"
                aria-label="Last page"
              >
                »
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(DetectionTable);
