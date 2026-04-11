// @ts-nocheck
// See https://kit.svelte.dev/docs/types#app
// for information about these interfaces
import type { Writable } from 'svelte/store';

declare module 'svelte' {
	export function getContext(key: 'i18n'): Writable<any>;
}

declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface Platform {}
	}
}

export {};
