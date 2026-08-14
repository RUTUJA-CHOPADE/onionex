import adapter from '@sveltejs/adapter-auto';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules')
						? undefined
						: true
			},

			adapter: adapter()
		})
	],

	server: {
		host: '127.0.0.1',
		port: 5173,

		allowedHosts: ['onionexplorer.local'],

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