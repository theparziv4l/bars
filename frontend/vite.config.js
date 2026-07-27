import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        port: 3000,
        host: true,
        watch: {
            usePolling: process.env.WATCH_USE_POLLING === 'true',
        },
        proxy: {
            '/api': {
                target: 'http://backend:8000',
                changeOrigin: true,
            },
            '/ws': {
                target: 'ws://backend:8000',
                ws: true,
            },
        },
    },
    build: {
        rollupOptions: {
            output: {
                manualChunks: {
                    vendor: ['react', 'react-dom', 'react-router-dom'],
                    flow: ['reactflow'],
                    ui: ['lucide-react'],
                },
            },
        },
        sourcemap: true,
    },
})
