import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Vite needs to know react-pdf's worker uses the ?url import pattern
  worker: {
    format: "es",
  },
  optimizeDeps: {
    // Pre-bundle pdfjs-dist so Vite doesn't trip over its CJS/ESM mix
    include: ["pdfjs-dist"],
    exclude: ["pdfjs-dist/build/pdf.worker.min.js"],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    historyApiFallback: true,
    proxy: {
      "/api": {
        target: process.env.VITE_API_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
