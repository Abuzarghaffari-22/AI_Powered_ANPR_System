import type { AppProps } from "next/app";
import { JetBrains_Mono, Sora } from "next/font/google";
import React, { useEffect, useState } from "react";
import "../styles/globals.css";

const sora = Sora({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sora",
  weight: ["300", "400", "500", "600", "700", "800"],
  preload: true,
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains",
  weight: ["400", "500", "600"],
  preload: false,
});

/* Global error boundary — prevents a render crash from showing a blank screen */
interface EBState { hasError: boolean; message: string }
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  EBState
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, message: "" };
  }
  static getDerivedStateFromError(err: Error): EBState {
    return { hasError: true, message: err?.message ?? "Unknown error" };
  }
  componentDidCatch(err: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary]", err, info.componentStack);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: "1rem",
            padding: "2rem",
            background: "var(--bg, #0f0f0f)",
            color: "var(--text, #fff)",
          }}
        >
          <p style={{ fontSize: "1.1rem", fontWeight: 700 }}>
            Something went wrong
          </p>
          <p style={{ fontSize: "0.85rem", opacity: 0.6 }}>
            {this.state.message}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, message: "" });
              window.location.reload();
            }}
            style={{
              padding: "0.5rem 1.5rem",
              borderRadius: "0.5rem",
              background: "#0B6E4F",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App({ Component, pageProps }: AppProps) {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const saved = localStorage.getItem("anpr_theme") as "light" | "dark" | null;
    const preferred = window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
    const resolved = saved ?? preferred;
    setTheme(resolved);
    document.documentElement.setAttribute("data-theme", resolved);
  }, []);

  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("anpr_theme", next);
  };

  return (
    <ErrorBoundary>
      <div
        className={`${sora.variable} ${jetbrains.variable}`}
        style={{ height: "100%" }}
      >
        <Component {...pageProps} toggleTheme={toggleTheme} theme={theme} />
      </div>
    </ErrorBoundary>
  );
}
