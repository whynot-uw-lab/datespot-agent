import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 10003,
    strictPort: true,
    proxy: {
      "/health": "http://127.0.0.1:20003",
      "/reports": "http://127.0.0.1:20003",
      "/runs": {
        target: "http://127.0.0.1:20003",
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
