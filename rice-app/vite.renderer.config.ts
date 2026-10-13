import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Browser development without launching Electron. The renderer connects to
// VITE_RICE_RUNTIME_URL, or http://localhost:8080 when no override is set.
// Start with: npx vite --config vite.renderer.config.ts
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
