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
          // Todo cuadrado: DaisyUI usa estas variables para redondear
          // botones, tarjetas, inputs, badges y tabs -- en 0 queda todo
          // en ángulo recto.
          "--rounded-box": "0",
          "--rounded-btn": "0",
          "--rounded-badge": "0",
          "--tab-radius": "0",
        },
      },
      {
        "descuentito-dark": {
          primary: "#a78bfa",
          "primary-content": "#1f1235",
          secondary: "#7c8798",
          accent: "#fb923c",
          neutral: "#172420",
          "base-100": "#0d1715",
          "base-200": "#152321",
          "base-300": "#223531",
          "base-content": "#e8f0ee",
          info: "#4aa8ff",
          success: "#3ecf8e",
          warning: "#ffb84a",
          error: "#ff6b4a",
          "--rounded-box": "0",
          "--rounded-btn": "0",
          "--rounded-badge": "0",
          "--tab-radius": "0",
        },
      },
    ],
    darkTheme: "descuentito-dark",
  },
};
