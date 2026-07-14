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
          // Vite's own dynamic-import preload helper is a virtual module
          // (no "node_modules" in its id), so it falls outside every rule
          // below and Rollup's automatic splitting was folding it into
          // vendor-charts too — the one true "every route needs this"
          // dependency, forcing the same eager-load problem as React did.
          if (id.includes("vite/preload-helper")) return "vendor-react";
          if (!id.includes("node_modules")) return;
          // React itself must be pinned ahead of the per-feature vendor
          // buckets below. Left unassigned, Rollup's automatic splitting
          // doesn't necessarily put it in the shared "global" chunk — it
          // was observed landing inside vendor-charts instead, since that
          // chunk (like every page) needs React too. That forced every
          // route, including ones that never touch charts, to eagerly
          // download and execute the entire ~1.6MB charting/graph stack
          // just to get React.
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/")) return "vendor-react";
          // Only carve out the heavy libraries used by a small subset of
          // pages, so they get their own cacheable chunk instead of being
          // inlined into whichever single page happens to import them
          // first. Everything else (antd core, axios, dayjs, react-router)
          // is left to Rollup's own automatic splitting, which already does
          // a better job than a blanket catch-all bucket would (it
          // separates per shared-import-boundary rather than dumping every
          // remaining dependency into one chunk that every page would then
          // have to load eagerly).
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
