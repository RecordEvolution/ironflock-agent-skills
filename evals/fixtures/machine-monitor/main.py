"""Machine monitor: samples three machines and publishes their state and measurements."""

import asyncio
import os
import random
from datetime import datetime, timezone

from ironflock import IronFlock

MACHINES = ["press-1", "press-2", "lathe-1"]
INTERVAL = float(os.environ.get("SAMPLE_INTERVAL", "5"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def set_speed(machine_id: str, rpm: float):
    """Called from the board or an AI agent to change a machine's target speed."""
    await flock.publish_to_table("machine_status", {
        "tsp": now(), "machine_id": machine_id, "target_rpm": rpm,
    })
    return f"{machine_id} target speed set to {rpm} rpm"


async def main():
    await flock.register_device_function("set_speed", set_speed)
    while True:
        for machine_id in MACHINES:
            running = random.random() > 0.1
            await flock.publish_to_table("measurements", {
                "tsp": now(),
                "machine_id": machine_id,
                "temperature": round(random.uniform(40, 90), 1),
                "vibration": round(random.uniform(0.1, 4.0), 2),
                "power_kw": round(random.uniform(2, 15), 2) if running else 0.0,
            })
            await flock.publish_to_table("machine_status", {
                "tsp": now(),
                "machine_id": machine_id,
                "state": "RUNNING" if running else random.choice(["IDLE", "FAULT"]),
            })
        await asyncio.sleep(INTERVAL)


flock = IronFlock(mainFunc=main)
flock.run()
