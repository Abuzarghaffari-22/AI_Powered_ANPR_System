/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sora)", "system-ui", "sans-serif"],
        display: ["var(--font-sora)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains)", "monospace"],
      },

      boxShadow: {
        brand: "0 4px 20px rgba(11, 110, 79, 0.25)",
        "brand-lg": "0 8px 32px rgba(11, 110, 79, 0.30)",
        card: "0 1px 4px rgba(1, 65, 28, 0.06), 0 1px 2px rgba(1, 65, 28, 0.04)",
        "card-md":
          "0 4px 12px rgba(1, 65, 28, 0.08), 0 1px 4px rgba(1, 65, 28, 0.04)",
      },
      animation: {
        "fade-up": "fade-up 0.25s cubic-bezier(0.16, 1, 0.3, 1) both",
        "fade-in": "fade-in 0.2s ease both",
        "scale-in": "scale-in 0.2s cubic-bezier(0.16, 1, 0.3, 1) both",
        shimmer: "shimmer 1.8s infinite linear",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.97) translateY(6px)" },
          to: { opacity: "1", transform: "scale(1) translateY(0)" },
        },
      },
      transitionTimingFunction: {
        spring: "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      transitionDuration: {
        250: "250ms",
        350: "350ms",
      },
    },
  },
  plugins: [],
};
