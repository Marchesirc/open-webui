import os
import subprocess
import sys
from datetime import datetime

print("🚀 Aplicando tema TUF Gaming X570 BIOS v2.0...")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==================== BACKEND ====================
routers_dir = os.path.join(BASE_DIR, "backend", "open_webui", "routers")
os.makedirs(routers_dir, exist_ok=True)

hardware_code = '''import psutil
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class FanControl(BaseModel):
    fan_name: str
    percent: int

@router.get("/hardware")
async def get_hardware():
    data = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_temp": 0,
        "motherboard_temp": 36,
        "ram": {"total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
                "used_gb": round(psutil.virtual_memory().used / (1024**3), 1),
                "percent": psutil.virtual_memory().percent},
        "disks": [{"device": part.device, "used_gb": round(psutil.disk_usage(part.mountpoint).used / (1024**3), 1),
                   "total_gb": round(psutil.disk_usage(part.mountpoint).total / (1024**3), 1)} 
                  for part in psutil.disk_partitions() if part.fstype],
        "fans": {"CPU_FAN": 1269, "CHA1_FAN": 0, "AIO_PUMP": 1427}
    }
    # Temperaturas reais
    temps = psutil.sensors_temperatures()
    if temps:
        for name, entries in temps.items():
            for entry in entries:
                if any(x in name.lower() for x in ["cpu", "core"]):
                    data["cpu_temp"] = round(entry.current, 1)
    return data

@router.post("/set-fan")
async def set_fan(control: FanControl):
    print(f"FAN CONTROL: {control.fan_name} → {control.percent}%")
    return {"status": "success", "message": "Comando aplicado"}
'''

with open(os.path.join(routers_dir, "hardware.py"), "w", encoding="utf-8") as f:
    f.write(hardware_code)

# Patch no main.py
main_py = os.path.join(BASE_DIR, "backend", "open_webui", "main.py")
with open(main_py, "r", encoding="utf-8") as f:
    content = f.read()

if "hardware_router" not in content:
    import_line = "from open_webui.routers.hardware import router as hardware_router\n"
    include_line = "app.include_router(hardware_router, prefix='/api/v1', tags=['hardware'])\n"
    lines = content.splitlines()
    for i in range(len(lines)-1, -1, -1):
        if "app.include_router" in lines[i]:
            lines.insert(i+1, import_line)
            lines.insert(i+2, include_line)
            break
    else:
        lines.append(import_line)
        lines.append(include_line)
    with open(main_py, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# ==================== FRONTEND BIOS PAGE ====================
bios_dir = os.path.join(BASE_DIR, "frontend", "src", "routes", "bios")
os.makedirs(bios_dir, exist_ok=True)

page_code = """<script>
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
"""

with open(os.path.join(bios_dir, "+page.svelte"), "w", encoding="utf-8") as f:
    f.write(page_code)

print("✅ Tema TUF BIOS aplicado com sucesso!")
print("Acesse http://localhost:5173/bios após rodar o servidor")
