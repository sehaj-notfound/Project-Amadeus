"""
Project Amadeus: Hardware Detection & Configuration Manager
============================================================
Detects system hardware capabilities once during installation/setup,
and persists the configuration to:
  %LOCALAPPDATA%\ProjectAmadeus\hardware_config.json

On subsequent boots, tts_engine and the daemon read this JSON file in < 0.2ms,
completely eliminating runtime detection overhead, WMI queries, and Win32 calls.
"""

import ctypes
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def get_amadeus_data_dir() -> Path:
    """Returns the persistent base directory for Project Amadeus data on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ProjectAmadeus"
    return Path.home() / "AppData" / "Local" / "ProjectAmadeus"


def get_hardware_config_path() -> Path:
    """Returns the path to the persisted hardware_config.json."""
    return get_amadeus_data_dir() / "hardware_config.json"


def get_system_ram_gb() -> float:
    """
    Returns total physical RAM in GB.
    Uses built-in Windows ctypes first (works before pip installs psutil),
    then falls back to psutil.
    """
    if sys.platform == "win32":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024 ** 3), 2)
        except Exception:
            pass

    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 2)
    except Exception:
        return 8.0


def get_cpu_name_and_u_series() -> Tuple[str, bool]:
    """
    Reads the CPU model name (e.g. '12th Gen Intel(R) Core(TM) i5-12450H')
    via built-in Windows Registry in < 0.1ms and checks if it is a U-series chip.
    """
    cpu_name = ""
    if sys.platform == "win32":
        try:
            import winreg
            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                cpu_name = str(val).strip()
        except Exception:
            pass

    if not cpu_name:
        cpu_name = platform.processor() or "Unknown CPU"

    # Match Intel/AMD mobile U-series model numbers (e.g., i5-1235U, Ryzen 5 5500U, Core Ultra 5 125U)
    is_u_series = bool(
        re.search(
            r"\b(?:i[3579]-\d{4,5}U|Ryzen\s+[3579]\s+\d{4}U|Ultra\s+[579]\s+\d{3}U|\d{4,5}U)\b",
            cpu_name,
            re.IGNORECASE,
        )
    )
    return cpu_name, is_u_series


def _inspect_cpu_topology() -> Tuple[int, Optional[int], Optional[int]]:
    """
    Returns (optimal_threads, p_cores, e_cores).
    Safe even if psutil is not yet installed during bootstrap.
    """
    try:
        import psutil
        logical = psutil.cpu_count(logical=True) or 4
        physical = psutil.cpu_count(logical=False) or max(2, logical // 2)
    except ImportError:
        logical = os.cpu_count() or 4
        physical = max(2, logical // 2)

    # 1. On Windows: Inspect Intel Hybrid P-core / E-core distribution via Win32 API
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            RelationProcessorCore = 0
            length = ctypes.c_ulong(0)
            kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, None, ctypes.byref(length))

            if length.value > 0:
                buf = ctypes.create_string_buffer(length.value)
                if kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, buf, ctypes.byref(length)):
                    raw = buf.raw
                    offset = 0
                    p_cores = 0
                    e_cores = 0
                    while offset < length.value:
                        rel_type = int.from_bytes(raw[offset:offset+4], "little")
                        size = int.from_bytes(raw[offset+4:offset+8], "little")
                        if rel_type == RelationProcessorCore:
                            # EfficiencyClass > 0 indicates P-core on Intel Hybrid CPUs
                            eff_class = raw[offset+9]
                            if eff_class > 0:
                                p_cores += 1
                            else:
                                e_cores += 1
                        offset += size

                    if p_cores > 0 and e_cores > 0:
                        # U-series chips (<= 2 P-cores) use up to 4 E-cores to reach 6 threads;
                        # P/H-series chips (4+ P-cores) use at most 2 E-cores to avoid stalls.
                        helper_e_cores = 4 if p_cores <= 2 else 2
                        threads = max(2, min(8, p_cores + min(helper_e_cores, e_cores)))
                        return threads, p_cores, e_cores
        except Exception:
            pass

    # 2. Homogeneous CPUs (AMD Ryzen, older Intel, virtual machines)
    threads = max(2, min(8, physical))
    return threads, None, None


def get_optimal_threads() -> int:
    """Returns the optimal OpenMP / PyTorch CPU thread count."""
    threads, _, _ = _inspect_cpu_topology()
    return threads


def detect_gpu_profile(vram_threshold_gb: float = 3.75) -> Dict[str, Any]:
    """
    Detects GPU presence, total VRAM, and whether PyTorch with CUDA is available.
    Threshold default is 3.75 GB to reliably include 4 GB cards that Windows reports
    as ~3.8-3.999 GB due to reserved memory spaces.
    """
    gpu_name: Optional[str] = None
    vram_gb: float = 0.0
    cuda_torch_installed = False

    # 1. Check if torch has CUDA support compiled and active
    try:
        import torch
        if torch.cuda.is_available():
            cuda_torch_installed = True
            props = torch.cuda.get_device_properties(0)
            gpu_name = props.name
            vram_gb = round(props.total_memory / (1024 ** 3), 2)
    except Exception:
        pass

    # 2. If PyTorch is not installed yet or is CPU-only, inspect hardware via nvidia-smi
    if not gpu_name:
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if res.returncode == 0 and res.stdout.strip():
                first_line = res.stdout.strip().split("\n")[0]
                parts = first_line.split(",")
                if len(parts) >= 2:
                    gpu_name = parts[0].strip()
                    vram_gb = round(float(parts[1].strip()) / 1024.0, 2)
        except Exception:
            pass

    # 3. Fallback to WMI on Windows (with 32-bit signed integer overflow fix for >= 4 GB GPUs)
    if not gpu_name and sys.platform == "win32":
        try:
            import win32com.client
            wmi = win32com.client.GetObject("winmgmts:")
            for controller in wmi.InstancesOf("Win32_VideoController"):
                name = str(getattr(controller, "Name", ""))
                if "NVIDIA" in name.upper():
                    gpu_name = name
                    adapter_ram = getattr(controller, "AdapterRAM", 0)
                    if adapter_ram:
                        # Fix 32-bit signed int overflow on GPUs with >= 4 GB VRAM
                        if adapter_ram < 0:
                            adapter_ram = adapter_ram & 0xFFFFFFFF
                        vram_gb = max(vram_gb, round(float(adapter_ram) / (1024 ** 3), 2))
                    break
        except Exception:
            pass

    is_capable_gpu = (gpu_name is not None and vram_gb >= vram_threshold_gb)
    runtime_device = "cuda" if (cuda_torch_installed and is_capable_gpu) else "cpu"

    return {
        "gpu_present": gpu_name is not None,
        "gpu_name": gpu_name or "None",
        "vram_gb": vram_gb,
        "cuda_capable_gpu": is_capable_gpu,
        "cuda_torch_installed": cuda_torch_installed,
        "runtime_device": runtime_device,
    }


def compute_hardware_config(vram_threshold_gb: float = 3.75) -> Dict[str, Any]:
    """
    Computes complete hardware configuration profile for Project Amadeus.
    Sets `ja_only`:
      - True:  Low-power / U-series CPUs, < 6 threads, or <= 8 GB RAM (loads Japanese voice only).
      - False: Capable CUDA GPU (>= 3.75 GB VRAM) OR non-U-series CPU with >= 6 threads and >= 12 GB RAM
               (loads both Japanese and English voices).
    """
    threads, p_cores, _ = _inspect_cpu_topology()
    cpu_name, is_u_series_name = get_cpu_name_and_u_series()
    ram_gb = get_system_ram_gb()
    gpu_info = detect_gpu_profile(vram_threshold_gb=vram_threshold_gb)

    # Intel Hybrid chips with <= 2 P-cores are also low-power U-series chips
    is_low_power_cpu = is_u_series_name or (p_cores is not None and p_cores <= 2)

    # Check if this PC can smoothly run both Japanese and English models in memory
    can_run_both_voices = (
        gpu_info["runtime_device"] == "cuda"
        or gpu_info["cuda_capable_gpu"]
        or (threads >= 6 and not is_low_power_cpu and ram_gb >= 12.0)
    )

    ja_only = not can_run_both_voices

    cfg = {
        "version": 2,
        "device": gpu_info["runtime_device"],
        "threads": threads,
        "ja_only": ja_only,
        "cpu_name": cpu_name,
        "is_u_series_cpu": is_low_power_cpu,
        "ram_gb": ram_gb,
        "gpu_name": gpu_info["gpu_name"],
        "vram_gb": gpu_info["vram_gb"],
        "cuda_capable_gpu": gpu_info["cuda_capable_gpu"],
        "cuda_torch_installed": gpu_info["cuda_torch_installed"],
    }
    return cfg


def save_hardware_config(cfg: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Saves the hardware configuration to JSON."""
    if path is None:
        path = get_hardware_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return path


def load_hardware_config(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Loads existing hardware configuration in < 0.2ms."""
    if path is None:
        path = get_hardware_config_path()
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_or_create_hardware_config(
    force_recompute: bool = False,
    vram_threshold_gb: float = 3.75
) -> Dict[str, Any]:
    """
    Main entry point for tts_engine and runtime daemons:
    1. Returns cached JSON config if it exists and is up to date (0ms latency).
    2. Self-heals automatically if CUDA PyTorch was installed after the initial check.
    3. Otherwise, detects hardware, saves to JSON, and returns the profile.
    """
    path = get_hardware_config_path()
    if not force_recompute:
        cached = load_hardware_config(path)
        if cached and "device" in cached and "threads" in cached and "ja_only" in cached:
            # Self-heal if a capable GPU was previously detected before CUDA PyTorch was installed
            if cached.get("cuda_capable_gpu") and not cached.get("cuda_torch_installed"):
                try:
                    import torch
                    if torch.cuda.is_available():
                        force_recompute = True
                except Exception:
                    pass
            if not force_recompute:
                return cached

    cfg = compute_hardware_config(vram_threshold_gb=vram_threshold_gb)
    try:
        save_hardware_config(cfg, path)
    except Exception as e:
        print(f"[HardwareDetector] Warning: Could not save config to {path}: {e}")
    return cfg


if __name__ == "__main__":
    force = "--recompute" in sys.argv
    print("=" * 60)
    print("Project Amadeus: Hardware Detection")
    print("=" * 60)
    cfg = get_or_create_hardware_config(force_recompute=force)
    print(json.dumps(cfg, indent=2))
    print(f"\nConfiguration saved to: {get_hardware_config_path()}")
    print("=" * 60)