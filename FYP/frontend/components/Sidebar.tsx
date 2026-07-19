import React, { memo, useState } from "react";
import { api } from "../lib/api";
import type { User } from "../lib/types";

type SectionId = "overview" | "detections" | "vehicles" | "alerts";

interface NavItem {
  id: SectionId;
  label: string;
  icon: React.ReactElement;
}

const nav: NavItem[] = [
  {
    id: "overview",
    label: "Overview",
    icon: (
      <svg
        className="w-[18px] h-[18px]"
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
  },
  {
    id: "detections",
    label: "Detections",
    icon: (
      <svg
        className="w-[18px] h-[18px]"
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
  },
  {
    id: "vehicles",
    label: "Vehicles",
    icon: (
      <svg
        className="w-[18px] h-[18px]"
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
  },
  {
    id: "alerts",
    label: "Alerts",
    icon: (
      <svg
        className="w-[18px] h-[18px]"
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
  },
];

interface Props {
  active: SectionId;
  onChange: (s: SectionId) => void;
  alerts: number;
  onLogout: () => void;
  user: User | null;
  collapsed: boolean;
  onToggle: () => void;
}

function Sidebar({
  active,
  onChange,
  alerts,
  onLogout,
  user,
  collapsed,
  onToggle,
}: Props) {
  const [mounted, setMounted] = React.useState(false)
  React.useEffect(() => { setMounted(true) }, [])

  const displayUser = mounted ? user : null
  const initial = displayUser?.username?.[0]?.toUpperCase() ?? "A"

  const [showChangePwd, setShowChangePwd] = useState(false)
  const [pwdForm, setPwdForm]             = useState({ current: "", next: "", confirm: "" })
  const [pwdMsg, setPwdMsg]               = useState<{ text: string; ok: boolean } | null>(null)
  const [pwdLoading, setPwdLoading]       = useState(false)
  const [showCurrent, setShowCurrent]     = useState(false)
  const [showNext, setShowNext]           = useState(false)

  const openChangePwd = () => {
    setPwdForm({ current: "", next: "", confirm: "" })
    setPwdMsg(null)
    setShowChangePwd(true)
  }

  const submitChangePwd = async () => {
    setPwdMsg(null)
    if (pwdForm.next !== pwdForm.confirm) {
      setPwdMsg({ text: "New passwords do not match", ok: false })
      return
    }
    if (pwdForm.next.length < 12) {
      setPwdMsg({ text: "New password must be at least 12 characters", ok: false })
      return
    }
    setPwdLoading(true)
    try {
      const r = await api.changePassword(pwdForm.current, pwdForm.next)
      setPwdMsg({ text: r.message, ok: true })
      setPwdForm({ current: "", next: "", confirm: "" })
      setTimeout(() => setShowChangePwd(false), 1500)
    } catch (e) {
      setPwdMsg({ text: e instanceof Error ? e.message : "Failed", ok: false })
    } finally {
      setPwdLoading(false)
    }
  }

  return (
    <>
      <aside
        className="flex flex-col shrink-0 sidebar-transition relative z-10"
        style={{
          width: collapsed ? "68px" : "232px",
          background: "var(--surface)",
          borderRight: "1px solid var(--border)",
          boxShadow: "1px 0 0 var(--border)",
        }}
        aria-label="Main navigation"
      >
      <div
        className="flex items-center px-4 py-4 shrink-0"
        style={{
          borderBottom: "1px solid var(--border)",
          height: "64px",
          justifyContent: collapsed ? "center" : "space-between",
        }}
      >
        {!collapsed && (
          <div className="flex items-center gap-3 overflow-hidden">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
              style={{
                background: "var(--brand)",
                boxShadow: "var(--shadow-brand)",
              }}
            >
              <svg
                className="w-4 h-4 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15 10l4.553-2.069A1 1 0 0121 8.82V15a1 1 0 01-.553.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
                />
              </svg>
            </div>
            <div className="overflow-hidden">
              <div
                className="font-bold text-sm leading-none mb-0.5 tracking-tight whitespace-nowrap"
                style={{ color: "var(--text)" }}
              >
                ANPR System
              </div>
              <div
                className="text-xs whitespace-nowrap"
                style={{ color: "var(--text-3)" }}
              >
                Security Dashboard
              </div>
            </div>
          </div>
        )}

        {collapsed && (
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: "var(--brand)" }}
          >
            <svg
              className="w-4 h-4 text-white"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M15 10l4.553-2.069A1 1 0 0121 8.82V15a1 1 0 01-.553.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
              />
            </svg>
          </div>
        )}

        {!collapsed && (
          <button
            onClick={onToggle}
            className="w-7 h-7 rounded-lg flex items-center justify-center transition-colors shrink-0 focus-ring"
            style={{
              background: "var(--surface-el)",
              border: "1px solid var(--border)",
              color: "var(--text-3)",
              cursor: "pointer",
            }}
            aria-label="Collapse sidebar"
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
                d="M11 19l-7-7 7-7M18 19l-7-7 7-7"
              />
            </svg>
          </button>
        )}
      </div>

      {collapsed && (
        <div className="px-3 pt-3">
          <button
            onClick={onToggle}
            className="w-full h-8 rounded-lg flex items-center justify-center transition-colors focus-ring"
            style={{
              background: "var(--surface-el)",
              border: "1px solid var(--border)",
              color: "var(--text-3)",
              cursor: "pointer",
            }}
            aria-label="Expand sidebar"
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
                d="M13 5l7 7-7 7M6 5l7 7-7 7"
              />
            </svg>
          </button>
        </div>
      )}

      <nav
        className="flex-1 py-4 overflow-y-auto overflow-x-hidden"
        style={{ padding: collapsed ? "16px 10px" : "16px 10px" }}
        aria-label="Sidebar navigation"
      >
        <div className="space-y-0.5">
          {!collapsed && (
            <p
              className="text-[0.65rem] font-bold tracking-[0.12em] uppercase px-3 pb-1.5 pt-1"
              style={{ color: "var(--text-4)" }}
            >
              Navigation
            </p>
          )}

          {nav.map((item) => {
            const isActive = active === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onChange(item.id)}
                title={collapsed ? item.label : undefined}
                className="w-full flex items-center rounded-lg text-left text-sm font-medium transition-all duration-150 focus-ring relative group"
                style={{
                  gap: collapsed ? "0" : "10px",
                  padding: collapsed ? "10px" : "9px 12px",
                  justifyContent: collapsed ? "center" : "flex-start",
                  background: isActive ? "var(--brand-pale)" : "transparent",
                  color: isActive ? "var(--brand)" : "var(--text-2)",
                  border: isActive
                    ? "1px solid var(--brand-muted)"
                    : "1px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = "var(--surface-hover)";
                    e.currentTarget.style.color = "var(--text)";
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.background = "transparent";
                    e.currentTarget.style.color = "var(--text-2)";
                  }
                }}
                aria-current={isActive ? "page" : undefined}
              >
                {isActive && !collapsed && (
                  <span
                    className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full"
                    style={{ background: "var(--brand)" }}
                  />
                )}

                <span
                  className="shrink-0"
                  style={{ color: isActive ? "var(--brand)" : "inherit" }}
                >
                  {item.icon}
                </span>

                {!collapsed && <span className="flex-1">{item.label}</span>}

                {item.id === "alerts" && alerts > 0 && (
                  <span
                    className={`font-bold rounded-full text-white alert-pulse ${collapsed ? "absolute -top-0.5 -right-0.5" : "ml-auto"}`}
                    style={{
                      background: "var(--danger)",
                      fontSize: "0.6rem",
                      minWidth: collapsed ? "16px" : "18px",
                      height: collapsed ? "16px" : "18px",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      padding: "0 3px",
                    }}
                    aria-label={`${alerts} alerts`}
                  >
                    {alerts > 99 ? "99+" : alerts}
                  </span>
                )}

                {collapsed && (
                  <span
                    className="absolute left-full ml-3 px-2.5 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-50"
                    style={{
                      background: "var(--text)",
                      color: "var(--surface)",
                      boxShadow: "var(--shadow-md)",
                    }}
                  >
                    {item.label}
                    {item.id === "alerts" && alerts > 0 && (
                      <span
                        className="ml-1.5 font-bold"
                        style={{ color: "#f87171" }}
                      >
                        ({alerts})
                      </span>
                    )}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </nav>

      <div
        className="shrink-0"
        style={{
          borderTop: "1px solid var(--border)",
          padding: collapsed ? "12px 10px" : "12px 10px",
        }}
      >
        {!collapsed ? (
          <>
            <button
              onClick={openChangePwd}
              className="flex items-center gap-3 px-3 py-2 rounded-lg mb-1 w-full text-left transition-colors"
              style={{ background: "var(--surface-el)" }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--surface-hover)")}
              onMouseLeave={e => (e.currentTarget.style.background = "var(--surface-el)")}
              title="Change password"
              aria-label="Change password"
            >
              <div
                className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold shrink-0 text-white"
                style={{ background: "linear-gradient(135deg, var(--brand), var(--brand-mid))" }}
                aria-hidden="true"
              >
                {initial}
              </div>
              <div className="overflow-hidden flex-1">
                <div className="text-sm font-semibold truncate leading-tight" style={{ color: "var(--text)" }}>
                  {displayUser?.username ?? "admin"}
                </div>
                <div className="text-xs capitalize leading-tight" style={{ color: "var(--text-3)" }}>
                  {displayUser?.role ?? "Administrator"}
                </div>
              </div>
              <svg className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--text-4)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536M9 13l6.586-6.586a2 2 0 112.828 2.828L11.828 15.828a2 2 0 01-1.414.586H9v-2a2 2 0 01.586-1.414z" />
              </svg>
            </button>

            <button
              onClick={onLogout}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all duration-150 focus-ring"
              style={{
                color: "var(--text-3)",
                background: "transparent",
                cursor: "pointer",
                border: "none",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--danger-bg)";
                e.currentTarget.style.color = "var(--danger-text)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "var(--text-3)";
              }}
              aria-label="Sign out"
            >
              <svg
                className="w-4 h-4 shrink-0"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.75}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
                />
              </svg>
              Sign out
            </button>
          </>
        ) : (
          <button
            onClick={onLogout}
            title="Sign out"
            className="w-full h-9 rounded-lg flex items-center justify-center transition-all duration-150 focus-ring group relative"
            style={{
              color: "var(--text-3)",
              background: "transparent",
              border: "1px solid var(--border)",
              cursor: "pointer",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--danger-bg)";
              e.currentTarget.style.color = "var(--danger-text)";
              e.currentTarget.style.borderColor = "var(--danger-border)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--text-3)";
              e.currentTarget.style.borderColor = "var(--border)";
            }}
            aria-label="Sign out"
          >
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
                d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
              />
            </svg>
            <span
              className="absolute left-full ml-3 px-2.5 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-50"
              style={{
                background: "var(--text)",
                color: "var(--surface)",
                boxShadow: "var(--shadow-md)",
              }}
            >
              Sign out
            </span>
          </button>
        )}
      </div>
    </aside>

    {showChangePwd && (
      <div
        className="overlay"
        onClick={e => { if (e.target === e.currentTarget) setShowChangePwd(false) }}
        role="dialog"
        aria-modal="true"
        aria-label="Change password"
      >
        <div
          className="card rounded-2xl p-6 w-full mx-4 scale-in"
          style={{ maxWidth: "400px", background: "var(--surface)", borderColor: "var(--border-hi)", boxShadow: "var(--shadow-xl)" }}
        >
          <div className="flex items-center justify-between mb-5">
            <div>
              <h3 className="font-bold tracking-tight" style={{ fontSize: "1.0625rem", color: "var(--text)" }}>Change Password</h3>
              <p className="text-xs mt-0.5" style={{ color: "var(--text-3)" }}>Must be 12+ chars with upper, lower, digit &amp; symbol</p>
            </div>
            <button
              onClick={() => setShowChangePwd(false)}
              className="w-8 h-8 rounded-xl flex items-center justify-center btn-ghost p-0"
              aria-label="Close"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="space-y-3">
            <div>
              <label className="block text-xs font-semibold mb-1.5" style={{ color: "var(--text-2)" }}>Current Password</label>
              <div className="relative">
                <input
                  type={showCurrent ? "text" : "password"}
                  value={pwdForm.current}
                  onChange={e => setPwdForm(f => ({ ...f, current: e.target.value }))}
                  className="input-base"
                  style={{ paddingRight: "2.5rem" }}
                  placeholder="Enter current password"
                  autoComplete="current-password"
                />
                <button type="button" onClick={() => setShowCurrent(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-4)" }}
                  aria-label={showCurrent ? "Hide" : "Show"}>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    {showCurrent
                      ? <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                      : <><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></>}
                  </svg>
                </button>
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold mb-1.5" style={{ color: "var(--text-2)" }}>New Password</label>
              <div className="relative">
                <input
                  type={showNext ? "text" : "password"}
                  value={pwdForm.next}
                  onChange={e => setPwdForm(f => ({ ...f, next: e.target.value }))}
                  className="input-base"
                  style={{ paddingRight: "2.5rem" }}
                  placeholder="12+ chars, upper, lower, digit, symbol"
                  autoComplete="new-password"
                />
                <button type="button" onClick={() => setShowNext(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-4)" }}
                  aria-label={showNext ? "Hide" : "Show"}>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    {showNext
                      ? <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                      : <><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></>}
                  </svg>
                </button>
              </div>
                  {pwdForm.next.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-1.5">
                  {([
                    ["12+ chars",  pwdForm.next.length >= 12],
                    ["Uppercase",  /[A-Z]/.test(pwdForm.next)],
                    ["Lowercase",  /[a-z]/.test(pwdForm.next)],
                    ["Digit",      /\d/.test(pwdForm.next)],
                    ["Symbol",     /[!@#$%^&*()_+\-=\[\]{}|;':,./<>?]/.test(pwdForm.next)],
                  ] as [string, boolean][]).map(([label, pass]) => (
                    <span key={label} className="text-[0.65rem] font-semibold px-2 py-0.5 rounded-full"
                      style={{
                        background:  pass ? "var(--success-bg)"     : "var(--surface-hi)",
                        color:       pass ? "var(--success-text)"   : "var(--text-4)",
                        border:      `1px solid ${pass ? "var(--success-border)" : "var(--border)"}`,
                      }}>
                      {pass ? "✓" : "○"} {label}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="block text-xs font-semibold mb-1.5" style={{ color: "var(--text-2)" }}>Confirm New Password</label>
              <input
                type="password"
                value={pwdForm.confirm}
                onChange={e => setPwdForm(f => ({ ...f, confirm: e.target.value }))}
                className="input-base"
                placeholder="Repeat new password"
                autoComplete="new-password"
                style={{ borderColor: pwdForm.confirm && pwdForm.confirm !== pwdForm.next ? "var(--danger)" : undefined }}
              />
              {pwdForm.confirm && pwdForm.confirm !== pwdForm.next && (
                <p className="text-xs mt-1" style={{ color: "var(--danger-text)" }}>Passwords do not match</p>
              )}
            </div>

            {pwdMsg && (
              <div className="px-3 py-2.5 rounded-xl text-xs font-medium"
                style={{
                  background: pwdMsg.ok ? "var(--success-bg)"   : "var(--danger-bg)",
                  border:     `1px solid ${pwdMsg.ok ? "var(--success-border)" : "var(--danger-border)"}`,
                  color:      pwdMsg.ok ? "var(--success-text)" : "var(--danger-text)",
                }}>
                {pwdMsg.text}
              </div>
            )}
          </div>

          <div className="flex gap-3 mt-5 pt-4" style={{ borderTop: "1px solid var(--border)" }}>
            <button onClick={() => setShowChangePwd(false)} className="btn-ghost flex-1 py-2.5">Cancel</button>
            <button
              onClick={submitChangePwd}
              disabled={pwdLoading || !pwdForm.current || !pwdForm.next || pwdForm.next !== pwdForm.confirm}
              className="btn-primary flex-1 py-2.5"
            >
              {pwdLoading ? "Saving…" : "Update Password"}
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}

export default memo(Sidebar);
