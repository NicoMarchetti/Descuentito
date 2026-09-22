import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // El backend Flask corre en :5000; esto evita lidiar con CORS en dev.
      "/api": "http://localhost:5000",
    },
  },
});
