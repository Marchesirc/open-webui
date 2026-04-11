import * as child_process from 'node:child_process';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import fs from 'node:fs';

let adapterFactory;
let adapterOptions = {};

try {
	({ default: adapterFactory } = await import('@sveltejs/adapter-static'));
	adapterOptions = {
		pages: 'build',
		assets: 'build',
		fallback: 'index.html'
	};
} catch (error) {
	console.warn('Falling back to @sveltejs/adapter-auto for editor tooling:', error?.message ?? error);
	({ default: adapterFactory } = await import('@sveltejs/adapter-auto'));
}

/** @type {import('@sveltejs/kit').Config} */
const config = {
	// Consult https://kit.svelte.dev/docs/integrations#preprocessors
	// for more information about preprocessors
	preprocess: vitePreprocess(),
	kit: {
		// adapter-auto only supports some environments, see https://kit.svelte.dev/docs/adapter-auto for a list.
		// If your environment is not supported or you settled on a specific environment, switch out the adapter.
		// See https://kit.svelte.dev/docs/adapters for more information about adapters.
		adapter: adapterFactory(adapterOptions),
		// poll for new version name every 60 seconds (to trigger reload mechanic in +layout.svelte)
		version: {
			name: (() => {
				try {
					return child_process.execSync('git rev-parse HEAD').toString().trim();
				} catch {
					// if git is not available, fallback to package.json version
					// or current timestamp
					try {
						return (
							JSON.parse(fs.readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
								?.version || Date.now().toString()
						);
					} catch {
						return Date.now().toString();
					}
				}
			})(),
			pollInterval: 60000
		}
	},
	vitePlugin: {
		// inspector: {
		// 	toggleKeyCombo: 'meta-shift', // Key combination to open the inspector
		// 	holdMode: false, // Enable or disable hold mode
		// 	showToggleButton: 'always', // Show toggle button ('always', 'active', 'never')
		// 	toggleButtonPos: 'bottom-right' // Position of the toggle button
		// }
	},
	onwarn: (warning, handler) => {
		const { code, message } = warning;
		const suppressedCodes = new Set([
			'css-unused-selector',
			'css_unused_selector',
			'export_let_unused',
			'element_invalid_self_closing_tag',
			'a11y_consider_explicit_label',
			'a11y_click_events_have_key_events',
			'a11y_no_static_element_interactions',
			'a11y_no_noninteractive_element_interactions',
			'a11y_interactive_supports_focus'
		]);

		if (suppressedCodes.has(code)) return;
		if (message?.includes("Also define the standard property 'appearance'")) return;
		if (message?.includes('Do not use empty rulesets')) return;

		handler(warning);
	}
};

export default config;
