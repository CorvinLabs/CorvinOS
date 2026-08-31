import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Frontend builds into ./dist/, served by FastAPI mount_static(...).
// dev-mode proxies API + auth + websocket through to the gateway on :8765
// so `npm run dev` lives at :5173 without CORS gymnastics.

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Site is served from /console/ at runtime — keep asset paths relative.
  base: "/console/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    minify: "terser",
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
        passes: 2,
      },
    },
    chunkSizeWarningLimit: 3000, // Mermaid chunk is ~2.8MB, that's ok for optional content
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Each group gets its own hash-named file → long-term browser caching.
          // Heavy optional deps are isolated so they don't invalidate the core bundle.
          if (id.includes("node_modules/mermaid")) return "mermaid";
          if (id.includes("node_modules/highlight.js")) return "highlight";
          if (id.includes("node_modules/katex")) return "katex";
          if (id.includes("node_modules/react-markdown") ||
              id.includes("node_modules/remark") ||
              id.includes("node_modules/rehype")) return "markdown";
          if (id.includes("node_modules/recharts") ||
              id.includes("node_modules/d3-") ||
              id.includes("node_modules/victory")) return "charts";
          if (id.includes("node_modules/@radix-ui")) return "radix";
          if (id.includes("node_modules/@tanstack")) return "tanstack";
          if (id.includes("node_modules/lucide-react")) return "icons";
          // React core stays in the main bundle (tiny, always needed)
        },
      },
    },
  },
  server: {
    port: 5173,
    // Serve app from /console/ base path in dev mode to match production config
    middlewareMode: false,
    proxy: {
      // Gateway target defaults to the live console on :8765, but can be
      // pointed at an isolated test instance via CORVIN_GATEWAY_URL so E2E
      // runs never touch the production gateway / its data.
      "/v1": {
        target: process.env.CORVIN_GATEWAY_URL || "http://127.0.0.1:8765",
        changeOrigin: false,
        ws: true,   // WebSocket upgrade proxying for the design-chat endpoint
      },
      "/api/console": {
        target: process.env.CORVIN_GATEWAY_URL || "http://127.0.0.1:8765",
        changeOrigin: false,
        rewrite: (path) => `/v1/console${path.substring("/api/console".length)}`,
      },
      "/api": {
        target: process.env.CORVIN_GATEWAY_URL || "http://127.0.0.1:8765",
        changeOrigin: false,
      },
      "/healthz": process.env.CORVIN_GATEWAY_URL || "http://127.0.0.1:8765",
    },
  },
});
