import daisyui from "daisyui";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["Space Grotesk", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [daisyui],
  daisyui: {
    themes: [
      {
        descuentito: {
          primary: "#6d28d9",
          "primary-content": "#ffffff",
          secondary: "#64748b",
          accent: "#f97316",
          neutral: "#1f2430",
          "base-100": "#f7f7fb",
          "base-200": "#ffffff",
          "base-300": "#e4e6ee",
          "base-content": "#1f2430",
          info: "#0284c7",
          success: "#16a34a",
          warning: "#d97706",
          error: "#dc2626",
        },
      },
      {
        "descuentito-dark": {
          primary: "#a78bfa",
          "primary-content": "#1f1235",
          secondary: "#7c8798",
          accent: "#fb923c",
          neutral: "#171e28",
          "base-100": "#10151c",
          "base-200": "#171e28",
          "base-300": "#262f3d",
          "base-content": "#e9edf2",
          info: "#4aa8ff",
          success: "#3ecf8e",
          warning: "#ffb84a",
          error: "#ff6b4a",
        },
      },
    ],
    darkTheme: "descuentito-dark",
  },
};
