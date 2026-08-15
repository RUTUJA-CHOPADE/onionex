import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const PORT = process.env.FRONTEND_PORT ? parseInt(process.env.FRONTEND_PORT) : 5173;
const HOST = process.env.FRONTEND_HOST || '0.0.0.0';
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:5000';

export default defineConfig({
	plugins: [
		sveltekit()
	],

	server: {
		host: HOST,
		port: PORT,

		allowedHosts: true,

		proxy: {
			'/api': {
				target: BACKEND_URL,
				changeOrigin: true
			},

			'/static': {
				target: BACKEND_URL,
				changeOrigin: true
			}
		}
	}
});