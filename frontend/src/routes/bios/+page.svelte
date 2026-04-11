<!-- @ts-nocheck -->
<script>
// @ts-nocheck
  import { onMount } from 'svelte';
  let hardware = {};
  let customOrange = '#ff8800';
  let bgColor = '#0a1428';

  async function loadHardware() {
    const res = await fetch('/api/v1/hardware');
    hardware = await res.json();
  }

  function applyColors() {
    document.documentElement.style.setProperty('--tuf-orange', customOrange);
    document.documentElement.style.setProperty('--tuf-bg', bgColor);
  }

  onMount(() => {
    loadHardware();
    setInterval(loadHardware, 3000);
  });
</script>

<div class="min-h-screen text-white font-mono p-6" style="background: linear-gradient(180deg, {bgColor} 0%, #1a2a4a 100%);">
  <div class="text-center text-3xl font-bold text-[#ff8800] mb-8">TUF GAMING X570-PLUS (BR) - BIOS Ver. 3001 - EZ Mode</div>
  
  <div class="grid grid-cols-3 gap-6">
    <div class="bg-[#1a2a4a] border-l-4 border-[#ff8800] p-6">
      <h2 class="text-[#ff8800]">CPU Temperature</h2>
      <div class="text-6xl font-black">{hardware.cpu_temp || 45}°C</div>
    </div>
    <div class="bg-[#1a2a4a] border-l-4 border-[#ff8800] p-6">
      <h2 class="text-[#ff8800]">RAM Usage</h2>
      <div class="text-6xl font-black">{hardware.ram?.percent || 0}%</div>
    </div>
    <div class="bg-[#1a2a4a] border-l-4 border-[#ff8800] p-6">
      <h2 class="text-[#ff8800]">Storage</h2>
      {#each hardware.disks || [] as disk}
        <div>{disk.device} - {disk.used_gb}/{disk.total_gb} GB</div>
      {/each}
    </div>
  </div>

  <div class="fixed bottom-8 right-8 bg-[#1a2a4a] p-6 border border-[#ff8800]">
    <h3 class="text-[#ff8800]">🎨 Painel de Cores</h3>
    <input type="color" bind:value={customOrange} on:input={applyColors}>
    <input type="color" bind:value={bgColor} on:input={applyColors}>
  </div>
</div>
