import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// host: '0.0.0.0' заставляет Vite слушать ВСЕ интерфейсы (IPv4 + IPv6).
// Без этого на Windows Vite слушает только IPv6 [::]:3000,
// а Chrome ходит по IPv4 127.0.0.1:3000 и получает ERR_CONNECTION_REFUSED.
// strictPort: чтобы порт не сменился молча на 3001 если 3000 занят.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    strictPort: true,
    open: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
