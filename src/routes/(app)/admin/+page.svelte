<script lang="ts">
// @ts-nocheck
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		goto('/admin/users');
	});
</script>
