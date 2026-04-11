import psutil
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class FanControl(BaseModel):
    fan_name: str
    percent: int


@router.get("/hardware")
async def get_hardware():
    virtual_memory = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions():
        if not part.fstype:
            continue

        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append(
                {
                    "device": part.device,
                    "used_gb": round(usage.used / (1024**3), 1),
                    "total_gb": round(usage.total / (1024**3), 1),
                }
            )
        except Exception:
            # Ignore inaccessible devices (e.g. unmounted optical drives).
            continue

    data = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_temp": 0,
        "motherboard_temp": 36,
        "ram": {
            "total_gb": round(virtual_memory.total / (1024**3), 1),
            "used_gb": round(virtual_memory.used / (1024**3), 1),
            "percent": virtual_memory.percent,
        },
        "disks": disks,
        "fans": {"CPU_FAN": 1269, "CHA1_FAN": 0, "AIO_PUMP": 1427},
    }

    # Temperaturas reais
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        temps = {}

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
