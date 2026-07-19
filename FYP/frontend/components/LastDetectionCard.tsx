import { memo, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { LiveDetection } from "../lib/types";

function timeSince(ts: string | undefined): string {
  if (!ts) return "";
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3_600) return `${Math.floor(diff / 60)}m ago`;
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ConfidenceBar({ value, label }: { value: number; label: string }) {
  const pct = Math.round(value * 100);
  const color =
    pct >= 75 ? "var(--success)" : pct >= 50 ? "var(--warn)" : "var(--danger)";
  return (
    <div className="flex items-center gap-2.5">
      <span
        className="text-xs font-mono w-10 shrink-0 font-semibold"
        style={{ color: "var(--text-3)" }}
      >
        {label}
      </span>
      <div
        className="flex-1 h-1.5 rounded-full overflow-hidden"
        style={{ background: "var(--surface-hi)" }}
      >
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span
        className="text-xs tabular-nums font-semibold w-8 text-right shrink-0"
        style={{ color }}
      >
        {pct}%
      </span>
    </div>
  );
}

interface RegisterForm {
  owner_name: string;
  make: string;
  model: string;
  color: string;
  owner_cnic: string;
  dues: "Clear" | "Remaining";
}

const EMPTY_FORM: RegisterForm = {
  owner_name: "",
  make: "",
  model: "",
  color: "",
  owner_cnic: "",
  dues: "Clear",
};

function EmptyState() {
  return (
    <div className="card h-full flex flex-col">
      <div
        className="px-5 py-3.5 flex items-center gap-2.5"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <div
          className="w-2 h-2 rounded-full"
          style={{ background: "var(--text-4)" }}
        />
        <h3 className="text-sm font-semibold" style={{ color: "var(--text)" }}>
          Last Detection
        </h3>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8">
        <div
          className="w-14 h-14 rounded-2xl flex items-center justify-center"
          style={{
            background: "var(--surface-el)",
            border: "1.5px solid var(--border)",
          }}
        >
          <svg
            className="w-6 h-6"
            style={{ color: "var(--text-4)" }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15 10l4.553-2.069A1 1 0 0121 8.82V15a1 1 0 01-.553.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
            />
          </svg>
        </div>
        <div className="text-center">
          <p className="text-sm font-medium" style={{ color: "var(--text-3)" }}>
            Awaiting detection
          </p>
          <p className="text-xs mt-1" style={{ color: "var(--text-4)" }}>
            Live feed is active
          </p>
        </div>
      </div>
    </div>
  );
}

interface Props {
  detection: LiveDetection | null;
}

function LastDetectionCard({ detection }: Props) {
  const [, tick] = useState(0);
  const [showReg, setShowReg] = useState(false);
  const [regForm, setRegForm] = useState<RegisterForm>(EMPTY_FORM);
  const [regMsg, setRegMsg] = useState("");
  const [regLoading, setRegLoading] = useState(false);

  useEffect(() => {
    const iv = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(iv);
  }, []);

  useEffect(() => {
    setShowReg(false);
    setRegForm(EMPTY_FORM);
    setRegMsg("");
  }, [detection?.plate]);

  const handleRegister = async () => {
    if (!detection?.plate) return;
    if (!regForm.owner_name.trim()) {
      setRegMsg("Owner name is required");
      return;
    }
    setRegLoading(true);
    setRegMsg("");
    try {
      await api.registerPlate({
        license_number: detection.plate,
        owner_name: regForm.owner_name,
        make: regForm.make,
        model: regForm.model,
        color: regForm.color,
        owner_cnic: regForm.owner_cnic,
        dues: regForm.dues,
        status: "Authorized",
      });
      setRegMsg("Registered successfully!");
      setShowReg(false);
    } catch (e: unknown) {
      setRegMsg(`${e instanceof Error ? e.message : "Registration failed"}`);
    } finally {
      setRegLoading(false);
    }
  };

  if (!detection) return <EmptyState />;

  const auth = detection.status === "authorized";
  const v = detection.vehicle;

  const colors = auth
    ? {
      cardBg: "rgba(22, 163, 74, 0.03)",
      cardBorder: "rgba(22, 163, 74, 0.18)",
      badgeBg: "var(--success-bg)",
      badgeBorder: "var(--success-border)",
      badgeColor: "var(--success-text)",
      plate: "var(--success-text)",
      divider: "rgba(22, 163, 74, 0.12)",
      footerText: "var(--success-text)",
      dot: "var(--success)",
    }
    : {
      cardBg: "rgba(220, 38, 38, 0.03)",
      cardBorder: "rgba(220, 38, 38, 0.18)",
      badgeBg: "var(--danger-bg)",
      badgeBorder: "var(--danger-border)",
      badgeColor: "var(--danger-text)",
      plate: "var(--danger-text)",
      divider: "rgba(220, 38, 38, 0.12)",
      footerText: "var(--danger-text)",
      dot: "var(--danger)",
    };

  const vehicleRows: Array<[string, string | null | undefined]> = [
    ["Owner", v?.owner_name],
    ["Vehicle", [v?.make, v?.model].filter(Boolean).join(" ") || null],
    ["Colour", v?.color],
    ["Dues", v?.dues],
  ];

  return (
    <div
      className="rounded-xl h-full flex flex-col scale-in"
      style={{
        background: colors.cardBg,
        border: `1.5px solid ${colors.cardBorder}`,
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <div
        className="flex items-center justify-between px-5 py-3.5 shrink-0"
        style={{ borderBottom: `1px solid ${colors.divider}` }}
      >
        <div className="flex items-center gap-2.5">
          <span
            className="w-2 h-2 rounded-full"
            style={{
              background: colors.dot,
              boxShadow: auth
                ? "0 0 0 3px rgba(22,163,74,0.15)"
                : "0 0 0 3px rgba(220,38,38,0.15)",
            }}
          />
          <h3
            className="text-sm font-semibold"
            style={{ color: "var(--text)" }}
          >
            Last Detection
          </h3>
        </div>
        <span
          className="text-xs font-bold px-2.5 py-1 rounded-full tracking-wider"
          style={{
            background: colors.badgeBg,
            border: `1px solid ${colors.badgeBorder}`,
            color: colors.badgeColor,
          }}
        >
          {auth ? "✓ AUTHORIZED" : "✗ UNAUTHORIZED"}
        </span>
      </div>

      <div
        className="px-5 pt-4 pb-4 shrink-0"
        style={{ borderBottom: `1px solid ${colors.divider}` }}
      >
        <div
          className="plate-display font-extrabold mb-0.5"
          style={{
            fontSize: "2rem",
            color: colors.plate,
            letterSpacing: "0.10em",
          }}
        >
          {detection.plate || "—"}
        </div>
        <div
          className="flex items-center gap-2 text-xs mb-4"
          style={{ color: "var(--text-3)" }}
        >
          <span>{timeSince(detection.ts)}</span>
          <span>·</span>
          <span
            className="font-medium px-2 py-0.5 rounded-full"
            style={{
              background: "var(--surface-el)",
              border: "1px solid var(--border)",
              color: "var(--text-2)",
            }}
          >
            {detection.match_type || "none"}
          </span>
        </div>

        <div className="space-y-2">
          <ConfidenceBar value={detection.yolo_conf ?? 0} label="YOLO" />
          <ConfidenceBar value={detection.ocr_conf ?? 0} label="OCR" />
        </div>
      </div>

      <div className="px-5 py-4 flex-1 overflow-y-auto">
        {v ? (
          <div className="space-y-1">
            {vehicleRows.map(([label, val]) => (
              <div
                key={label}
                className="flex items-center justify-between py-1.5"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <span
                  className="text-xs font-medium"
                  style={{ color: "var(--text-3)" }}
                >
                  {label}
                </span>
                <span
                  className="text-xs font-semibold"
                  style={{
                    color:
                      label === "Dues" && val?.toLowerCase() !== "clear"
                        ? "var(--danger-text)"
                        : "var(--text)",
                  }}
                >
                  {val || "—"}
                </span>
              </div>
            ))}

            <div className="mt-3 pt-2 space-y-1.5">
              <p
                className="text-xs font-bold tracking-wide uppercase"
                style={{ color: "var(--text-3)" }}
              >
                Authorization
              </p>
              {[
                ["In database", true],
                ["Dues clear", v?.dues?.toLowerCase() === "clear"],
                ["Status active", detection.status === "authorized"],
              ].map(([label, pass]) => (
                <div
                  key={String(label)}
                  className="flex items-center justify-between text-xs py-1"
                >
                  <span style={{ color: "var(--text-3)" }}>
                    {String(label)}
                  </span>
                  <span
                    className="font-bold"
                    style={{ color: pass ? "var(--success)" : "var(--danger)" }}
                  >
                    {pass ? "✓" : "✗"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div
              className="flex items-start gap-2.5 p-3 rounded-xl"
              style={{
                background: "var(--danger-bg)",
                border: "1.5px solid var(--danger-border)",
              }}
            >
              <svg
                className="w-4 h-4 shrink-0 mt-0.5"
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
              <p
                className="text-xs leading-relaxed"
                style={{ color: "var(--danger-text)" }}
              >
                Plate not found in registry
              </p>
            </div>

            {!showReg ? (
              <button
                onClick={() => setShowReg(true)}
                className="btn-primary w-full text-xs"
                style={{ padding: "0.5rem" }}
              >
                + Register this vehicle
              </button>
            ) : (
              <div className="space-y-2">
                <p
                  className="text-xs font-semibold"
                  style={{ color: "var(--text-2)" }}
                >
                  Register:{" "}
                  <span
                    className="plate-display"
                    style={{ color: "var(--brand-mid)" }}
                  >
                    {detection.plate}
                  </span>
                </p>

                {(
                  [
                    ["owner_name", "Owner name *"],
                    ["make", "Make"],
                    ["model", "Model"],
                    ["color", "Colour"],
                    ["owner_cnic", "CNIC"],
                  ] as [keyof RegisterForm, string][]
                ).map(([key, label]) => (
                  <input
                    key={key}
                    placeholder={label}
                    value={regForm[key] as string}
                    onChange={(e) =>
                      setRegForm((f) => ({ ...f, [key]: e.target.value }))
                    }
                    className="input-base"
                    style={{ fontSize: "0.75rem", padding: "0.4rem 0.6rem" }}
                  />
                ))}

                <select
                  value={regForm.dues}
                  onChange={(e) =>
                    setRegForm((f) => ({
                      ...f,
                      dues: e.target.value as "Clear" | "Remaining",
                    }))
                  }
                  className="input-base"
                  style={{ fontSize: "0.75rem", padding: "0.4rem 0.6rem" }}
                >
                  <option value="Clear">Dues: Clear</option>
                  <option value="Remaining">Dues: Remaining</option>
                </select>

                {regMsg && (
                  <p
                    className="text-xs px-3 py-2 rounded-lg"
                    style={{
                      background: regMsg.startsWith("Registered")
                        ? "var(--success-bg)"
                        : "var(--danger-bg)",
                      color: regMsg.startsWith("Registered")
                        ? "var(--success-text)"
                        : "var(--danger-text)",
                      border: regMsg.startsWith("Registered")
                        ? "1px solid var(--success-border)"
                        : "1px solid var(--danger-border)",
                    }}
                  >
                    {regMsg}
                  </p>
                )}

                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => {
                      setShowReg(false);
                      setRegMsg("");
                    }}
                    className="btn-ghost flex-1 text-xs"
                    style={{ padding: "0.4rem" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleRegister}
                    disabled={regLoading}
                    className="btn-primary flex-1 text-xs"
                    style={{ padding: "0.4rem" }}
                  >
                    {regLoading ? "Saving…" : "Register"}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div
        className="px-5 py-2.5 flex items-center justify-between shrink-0"
        style={{ borderTop: `1px solid ${colors.divider}` }}
      >
        <span
          className="text-xs tabular-nums"
          style={{ color: "var(--text-3)" }}
        >
          {detection.ts ? new Date(detection.ts).toLocaleTimeString() : "—"}
        </span>
        <span
          className="text-xs font-bold px-2 py-0.5 rounded-full"
          style={{
            background: auth ? "var(--success-bg)" : "var(--danger-bg)",
            color: auth ? "var(--success-text)" : "var(--danger-text)",
          }}
        >
          {auth ? "Access granted" : "Access denied"}
        </span>
      </div>
    </div>
  );
}

export default memo(LastDetectionCard);
