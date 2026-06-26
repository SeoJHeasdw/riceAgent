import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Renderer-only dev config (electron-free).
//
// The default `vite.config.ts` loads `vite-plugin-electron`, which crashes under
// the installed Node toolchain and takes the renderer dev server down with it.
// This config drops every electron plugin so the React app runs in a plain
// browser. `lib/api.ts` falls back to scripted mock data when no runtime is
// connected; set `VITE_RICE_RUNTIME_URL` (see .env.local) to drive a locally-run
// rice-runtime over HTTP/SSE instead.
//
//   npx vite --config vite.renderer.config.ts
//
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@components': path.resolve(__dirname, './src/components'),
      '@screens': path.resolve(__dirname, './src/screens'),
      '@lib': path.resolve(__dirname, './src/lib'),
      '@styles': path.resolve(__dirname, './src/styles'),
      '@assets': path.resolve(__dirname, './src/assets'),
    },
  },
  server: {
    port: 5273,
    strictPort: true,
  },
});
