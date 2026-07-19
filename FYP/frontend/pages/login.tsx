import Head from "next/head";
import { useRouter } from "next/router";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { setAuth } from "../lib/auth";

/* Pakistan geometric SVG pattern */
function GeometricPattern() {
  return (
    <svg
      className="absolute inset-0 w-full h-full opacity-[0.07]"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <defs>
        <pattern
          id="geo"
          x="0"
          y="0"
          width="64"
          height="64"
          patternUnits="userSpaceOnUse"
        >
          {/* 8-pointed star motif — classical Islamic geometric */}
          <polygon
            points="32,4 36,20 52,20 40,30 44,46 32,36 20,46 24,30 12,20 28,20"
            fill="none"
            stroke="white"
            strokeWidth="0.8"
          />
          <rect
            x="20"
            y="20"
            width="24"
            height="24"
            fill="none"
            stroke="white"
            strokeWidth="0.5"
            transform="rotate(45 32 32)"
          />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#geo)" />
    </svg>
  );
}

/* System status pill  */
function StatusPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`w-1.5 h-1.5 rounded-full ${ok ? "bg-emerald-400" : "bg-red-400"}`}
        style={{ boxShadow: ok ? "0 0 6px #34d399" : "0 0 6px #f87171" }}
      />
      <span className="text-xs text-white/50 font-medium tracking-wide">
        {label}
      </span>
    </div>
  );
}

export default function Login() {
  const router = useRouter();
  const [form, setForm] = useState({ username: "", password: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPass, setShowPass] = useState(false);
  const [health, setHealth] = useState({ db: false, camera: false });

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        // Use same dynamic base as api.ts so LAN deployments work
        let base = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
        if (typeof window !== 'undefined') {
          const host = window.location.hostname;
          if (base.includes('localhost') && host !== 'localhost' && host !== '127.0.0.1') {
            base = `${window.location.protocol}//${host}:8000`;
          }
        }
        const res = await fetch(`${base}/api/health`, { cache: 'no-store' });
        if (res.ok && alive) {
          const d = await res.json() as { database?: boolean; camera_worker?: boolean };
          setHealth({ db: !!d.database, camera: !!d.camera_worker });
        }
      } catch { /* backend offline */ }
    };
    void check();
    return () => { alive = false; };
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await api.login(form.username, form.password);
      setAuth(data);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Head>
        <title>Sign In — ANPR Security System</title>
      </Head>

      <div className="min-h-screen flex">
        {/* Left brand panel */}
        <div
          className="hidden lg:flex lg:w-[42%] flex-col justify-between p-12 relative overflow-hidden"
          style={{
            background:
              "linear-gradient(160deg, #01411C 0%, #0B6E4F 60%, #01411C 100%)",
          }}
        >
          <GeometricPattern />

          {/* Top ambient glow */}
          <div
            className="absolute -top-24 -right-24 w-80 h-80 rounded-full pointer-events-none"
            style={{
              background:
                "radial-gradient(circle, rgba(255,255,255,0.06), transparent 70%)",
            }}
          />
          <div
            className="absolute bottom-0 left-0 w-64 h-64 pointer-events-none"
            style={{
              background:
                "radial-gradient(circle at bottom left, rgba(255,255,255,0.04), transparent 60%)",
            }}
          />

          {/* Logo */}
          <div className="relative z-10">
            <div className="flex items-center gap-3.5 mb-2">
              <div
                className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0"
                style={{
                  background: "rgba(255,255,255,0.12)",
                  border: "1px solid rgba(255,255,255,0.2)",
                  backdropFilter: "blur(8px)",
                }}
              >
                <svg
                  className="w-5 h-5 text-white"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.75}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M15 10l4.553-2.069A1 1 0 0121 8.82V15a1 1 0 01-.553.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
                  />
                </svg>
              </div>
              <div>
                <div className="text-white font-bold text-sm tracking-wide">
                  AI Powered ANPR System
                </div>
                <div className="text-white/50 text-xs">
                  Automated Plate Recognition
                </div>
              </div>
            </div>
          </div>

          {/* Hero copy */}
          <div className="relative z-10 space-y-6">
            <div>
              <p className="text-white/50 text-xs font-semibold tracking-[0.15em] uppercase mb-3">
                Smart City Infrastructure
              </p>
              <h1
                className="text-white font-extrabold leading-[1.12] tracking-tight"
                style={{ fontSize: "clamp(2rem, 3.5vw, 2.75rem)" }}
              >
                Intelligent
                <br />
                <span style={{ color: "#86EFAC" }}>License Plate</span>
                <br />
                Recognition
              </h1>
            </div>

            <p className="text-white/55 text-sm leading-relaxed max-w-xs">
              Real-time AI Powered ANPR monitoring with live camera feeds, instant
              unauthorized access alerts, and comprehensive vehicle registry
              management.
            </p>

            {/* Feature pills */}
            <div className="flex flex-wrap gap-2">
              {[
                "Vision Stream",
                "Neural Signals",
                "Critical Alerts",
                "Vehicle Nexus",
              ].map((f) => (
                <span
                  key={f}
                  className="text-xs font-medium px-3 py-1.5 rounded-full"
                  style={{
                    background: "rgba(255,255,255,0.10)",
                    border: "1px solid rgba(255,255,255,0.14)",
                    color: "rgba(255,255,255,0.75)",
                  }}
                >
                  {f}
                </span>
              ))}
            </div>
          </div>

          {/* System status */}
          <div className="relative z-10">
            <p className="text-white/30 text-xs font-semibold tracking-widest uppercase mb-3">
              System Health
            </p>
            <div className="flex gap-6">
              <StatusPill label="Vision Core" ok={health.camera} />
              <StatusPill label="Live Vision" ok={health.camera} />
              <StatusPill label="Data Pipeline" ok={health.db} />
            </div>
          </div>
        </div>

        {/* Right form panel */}
        <div
          className="flex-1 flex items-center justify-center px-6 py-12"
          style={{ background: "var(--bg)" }}
        >
          <div className="w-full max-w-[380px]">
            {/* Mobile logo */}
            <div
              className="flex lg:hidden items-center gap-3 mb-10"
              style={{ color: "var(--brand)" }}
            >
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{ background: "var(--brand)", color: "#fff" }}
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.75}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M15 10l4.553-2.069A1 1 0 0121 8.82V15a1 1 0 01-.553.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
                  />
                </svg>
              </div>
              <span className="font-bold text-sm">ANPR Security</span>
            </div>

            {/* Header */}
            <div className="mb-8 animate-fade-in">
              <h2
                className="font-extrabold tracking-tight mb-1.5"
                style={{ fontSize: "1.75rem", color: "var(--text)" }}
              >
                Welcome back
              </h2>
              <p className="text-sm" style={{ color: "var(--text-3)" }}>
                Sign in to access the security dashboard
              </p>
            </div>

            {/* Error */}
            {error && (
              <div
                className="mb-5 px-4 py-3 rounded-xl text-sm flex items-center gap-2.5 animate-fade-up"
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
                {error}
              </div>
            )}

            {/* Form */}
            <form
              onSubmit={submit}
              className="space-y-5 animate-fade-up"
              style={{ animationDelay: "80ms" }}
            >
              <div>
                <label
                  htmlFor="username"
                  className="block text-xs font-semibold mb-2 tracking-wide"
                  style={{ color: "var(--text-2)" }}
                >
                  Username
                </label>
                <div className="relative">
                  <div
                    className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                    style={{ color: "var(--text-4)" }}
                  >
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
                        d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                      />
                    </svg>
                  </div>
                  <input
                    id="username"
                    type="text"
                    required
                    autoComplete="username"
                    value={form.username}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, username: e.target.value }))
                    }
                    placeholder="Enter Username"
                    className="input-base focus-ring"
                    style={{ paddingLeft: "2.5rem" }}
                    aria-label="Username"
                  />
                </div>
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="block text-xs font-semibold mb-2 tracking-wide"
                  style={{ color: "var(--text-2)" }}
                >
                  Password
                </label>
                <div className="relative">
                  <div
                    className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                    style={{ color: "var(--text-4)" }}
                  >
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
                        d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                      />
                    </svg>
                  </div>
                  <input
                    id="password"
                    type={showPass ? "text" : "password"}
                    required
                    autoComplete="current-password"
                    value={form.password}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, password: e.target.value }))
                    }
                    placeholder="••••••••"
                    className="input-base focus-ring"
                    style={{ paddingLeft: "2.5rem", paddingRight: "2.75rem" }}
                    aria-label="Password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPass((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 transition-colors"
                    style={{ color: "var(--text-4)" }}
                    aria-label={showPass ? "Hide password" : "Show password"}
                  >
                    {showPass ? (
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
                          d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"
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
                          d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                        />
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"
                        />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="btn-primary w-full"
                style={{
                  padding: "0.75rem",
                  fontSize: "0.9375rem",
                  marginTop: "0.5rem",
                }}
                aria-label={loading ? "Signing in" : "Sign in"}
              >
                {loading ? (
                  <>
                    <svg
                      className="w-4 h-4 animate-spin"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      />
                    </svg>
                    Signing in…
                  </>
                ) : (
                  <>
                    Sign in
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
                        d="M13 7l5 5m0 0l-5 5m5-5H6"
                      />
                    </svg>
                  </>
                )}
              </button>
            </form>
          </div>
        </div>
      </div>
    </>
  );
}
