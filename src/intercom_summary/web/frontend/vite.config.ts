import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// Dev server proxies /api to the FastAPI backend so `npm run dev` works against it.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
