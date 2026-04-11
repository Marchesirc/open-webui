<script lang="ts">
	import Info from '$lib/components/icons/Info.svelte';

	type ErrorLike = {
		error?: { message?: string };
		detail?: string;
		message?: string;
	};

	export let content: string | ErrorLike | null = '';

	const getContentText = (value: string | ErrorLike | null): string => {
		if (typeof value === 'string') {
			return value;
		}

		if (value && typeof value === 'object') {
			if (value.error?.message) {
				return value.error.message;
			}

			if (value.detail) {
				return value.detail;
			}

			if (value.message) {
				return value.message;
			}

			return JSON.stringify(value);
		}

		return JSON.stringify(value);
	};
</script>

<div class="flex my-2 gap-2.5 border px-4 py-3 border-red-600/10 bg-red-600/10 rounded-lg">
	<div class=" self-start mt-0.5">
		<Info className="size-5 text-red-700 dark:text-red-400" />
	</div>

	<div class=" self-center text-sm">
		{getContentText(content)}
	</div>
</div>
