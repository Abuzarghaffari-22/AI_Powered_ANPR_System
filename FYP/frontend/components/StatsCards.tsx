import React, { memo, useEffect, useRef, useState } from "react";
import type { Stats } from "../lib/types";

function AnimatedNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(0);
  const prev = useRef(0);

  useEffect(() => {
    const start = prev.current;
    const end = value;
    const dur = 700;
    const startT = performance.now();
    let rafId: number;

    const step = (now: number) => {
      const t = Math.min((now - startT) / dur, 1);
      const eased = 1 - Math.pow(1 - t, 4); // ease-out-quart
      setDisplay(Math.round(start + (end - start) * eased));
      if (t < 1) rafId = requestAnimationFrame(step);
      else prev.current = end;
    };

    rafId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafId);
  }, [value]);

  return <>{display.toLocaleString()}</>;
}

type CardColor = "brand" | "success" | "danger" | "amber";

interface CardConfig {
  label: string;
  value: number | undefined;
  sub: string;
  subIcon: React.ReactElement;
  color: CardColor;
  icon: React.ReactElement;
  trend?: number;
}

function buildCards(s: Stats): CardConfig[] {
  const authRate =
    s.total_vehicles > 0
      ? Math.round((s.authorized_vehicles / s.total_vehicles) * 100)
      : 0;

  return [
    {
      label: "Total Vehicles",
      value: s.total_vehicles,
      sub: `${authRate}% authorized`,
      subIcon: <></>,
      color: "brand",
      icon: (
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
            d="M9 17a2 2 0 11-4 0 2 2 0 014 0zM19 17a2 2 0 11-4 0 2 2 0 014 0zM13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10h10zM9 16h10a1 1 0 001-1v-3l-3.5-4H13v7z"
          />
        </svg>
      ),
    },
    {
      label: "Authorized",
      value: s.authorized_vehicles,
      sub: `${s.total_vehicles - s.authorized_vehicles} unauthorized`,
      subIcon: <></>,
      color: "success",
      icon: (
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
            d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
          />
        </svg>
      ),
    },
    {
      label: "Today's Detections",
      value: s.total_detections_today,
      sub: `${s.authorized_today} cleared · ${s.unauthorized_today} denied`,
      subIcon: <></>,
      color: "amber",
      icon: (
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
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
          />
        </svg>
      ),
    },
    {
      label: "Total Detections",
      value: s.total_detections_all,
      sub: "All-time detection events",
      subIcon: <></>,
      color: "danger",
      icon: (
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
            d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"
          />
        </svg>
      ),
    },
  ];
}

const PALETTE: Record<
  CardColor,
  {
    bg: string;
    border: string;
    iconBg: string;
    iconColor: string;
    valueColor: string;
    dot: string;
  }
> = {
  brand: {
    bg: "var(--surface)",
    border: "var(--border)",
    iconBg: "var(--brand-pale)",
    iconColor: "var(--brand)",
    valueColor: "var(--brand)",
    dot: "var(--brand)",
  },
  success: {
    bg: "var(--surface)",
    border: "var(--border)",
    iconBg: "var(--success-bg)",
    iconColor: "var(--success)",
    valueColor: "var(--success-text)",
    dot: "var(--success)",
  },
  amber: {
    bg: "var(--surface)",
    border: "var(--border)",
    iconBg: "var(--warn-bg)",
    iconColor: "var(--warn)",
    valueColor: "var(--warn-text)",
    dot: "var(--warn)",
  },
  danger: {
    bg: "var(--surface)",
    border: "var(--border)",
    iconBg: "var(--danger-bg)",
    iconColor: "var(--danger)",
    valueColor: "var(--danger-text)",
    dot: "var(--danger)",
  },
};

function SkeletonCard() {
  return (
    <div className="card p-5" style={{ minHeight: "120px" }}>
      <div className="flex items-start justify-between mb-4">
        <div className="skeleton h-3 w-28 rounded" />
        <div className="skeleton w-9 h-9 rounded-lg" />
      </div>
      <div className="skeleton h-9 w-20 rounded mb-2" />
      <div className="skeleton h-3 w-36 rounded" />
    </div>
  );
}

interface Props {
  stats: Stats | null;
}

function StatsCards({ stats }: Props) {
  if (!stats) {
    return (
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 stagger-children">
        {[0, 1, 2, 3].map((i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 stagger-children">
      {buildCards(stats).map(({ label, value, sub, color, icon }, i) => {
        const p = PALETTE[color];
        return (
          <div
            key={label}
            className="card fade-up hover-lift p-5"
            style={{ animationDelay: `${i * 60}ms` }}
          >
              <div className="flex items-start justify-between mb-3">
              <p
                className="text-xs font-semibold tracking-wide leading-tight"
                style={{ color: "var(--text-3)", maxWidth: "70%" }}
              >
                {label}
              </p>
              <div
                className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
                style={{ background: p.iconBg, color: p.iconColor }}
                aria-hidden="true"
              >
                {icon}
              </div>
            </div>

            {/* Value */}
            <p
              className="font-extrabold mb-1.5 leading-none"
              style={{
                fontSize: "1.875rem",
                color: p.valueColor,
                fontVariantNumeric: "tabular-nums",
                letterSpacing: "-0.03em",
              }}
            >
              {value !== undefined ? <AnimatedNumber value={value} /> : "—"}
            </p>

            {/* Sub-label */}
            <p
              className="text-xs leading-relaxed"
              style={{ color: "var(--text-3)" }}
            >
              {sub}
            </p>
          </div>
        );
      })}
    </div>
  );
}

export default memo(StatsCards);
