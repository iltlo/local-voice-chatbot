from __future__ import annotations

import subprocess
from typing import Any


def get_vram_status() -> dict[str, Any]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except Exception:
        return {
            "vram_used_mb": None,
            "vram_total_mb": None,
            "vram_percent": None,
            "gpu_available": False,
        }

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return {
            "vram_used_mb": None,
            "vram_total_mb": None,
            "vram_percent": None,
            "gpu_available": False,
        }

    try:
        used_str, total_str = [part.strip() for part in lines[0].split(",", maxsplit=1)]
        used = int(used_str)
        total = int(total_str)
        percent = round((used / total) * 100, 1) if total > 0 else None
        return {
            "vram_used_mb": used,
            "vram_total_mb": total,
            "vram_percent": percent,
            "gpu_available": True,
        }
    except Exception:
        return {
            "vram_used_mb": None,
            "vram_total_mb": None,
            "vram_percent": None,
            "gpu_available": False,
        }
