import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@shared": path.resolve(__dirname, "src/shared"),
    },
  },
  build: {
    rollupOptions: {
      input: {
        digitizing: path.resolve(__dirname, "digitizing.html"),
        customer: path.resolve(__dirname, "customer.html"),
      },
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          // Only carve out the heavy libraries used by a small subset of
          // pages, so they get their own cacheable chunk instead of being
          // inlined into whichever single page happens to import them
          // first. Everything else (React, antd core, axios, dayjs,
          // react-router) is left to Rollup's own automatic splitting,
          // which already does a better job than a blanket catch-all
          // bucket would (it separates per shared-import-boundary rather
          // than dumping every remaining dependency into one chunk that
          // every page would then have to load eagerly).
          if (id.includes("openseadragon")) return "vendor-openseadragon";
          if (id.includes("@ant-design/charts") || id.includes("@antv")) return "vendor-charts";
          if (id.includes("react-split") || id.includes("split.js")) return "vendor-split";
          if (id.includes("@rjsf") || id.includes("ajv")) return "vendor-rjsf";
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
