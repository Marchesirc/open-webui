<script lang="ts">
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
		<svg class="size-5 text-red-700 dark:text-red-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
	</div>

	<div class=" self-center text-sm">
		{getContentText(content)}
	</div>
</div>
