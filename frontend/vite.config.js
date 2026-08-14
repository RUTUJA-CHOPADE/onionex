import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit()
	],

	server: {
		host: '0.0.0.0',
		port: 80,

		allowedHosts: true,

		proxy: {
			'/api': {
				target: 'http://127.0.0.1:5000',
				changeOrigin: true
			},

			'/static': {
				target: 'http://127.0.0.1:5000',
				changeOrigin: true
			}
		}
	}
});