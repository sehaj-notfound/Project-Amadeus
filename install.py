"""
Project Amadeus: Makise Kurisu Desktop AI Workstation
Automated 1-Click Installation & Hardware Bootstrap Script
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "Codebase"))

DEFAULT_HF_REPO = os.environ.get("AMADEUS_HF_REPO", "sehaj-notfound/amadeus-kurisu-voice")


def log_header(title: str):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def log_step(msg: str):
    print(f"[*] {msg}")


def log_success(msg: str):
    print(f"[+] {msg}")


def log_warn(msg: str):
    print(f"[!] {msg}")


def log_error(msg: str):
    print(f"[ERROR] {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pre-flight Checks (Antigravity CLI & Auth)
# ─────────────────────────────────────────────────────────────────────────────

def run_preflight_checks():
    log_header("STEP 1: PRE-FLIGHT SYSTEM VALIDATION")

    # 1. Verify Antigravity CLI binary
    log_step("Checking for Google Antigravity CLI ('agy')...")
    agy_path = shutil.which("agy")
    if not agy_path:
        log_error("Google Antigravity CLI ('agy') was not found on your system PATH!")
        print("\nProject Amadeus requires the Antigravity CLI as its cognitive engine.")
        print("Please ensure Antigravity CLI is installed and 'agy' is accessible from the command line.")
        sys.exit(1)
    log_success(f"Antigravity CLI detected: {agy_path}")

    # 2. Verify Google Account Authentication
    log_step("Checking Antigravity authentication status...")
    try:
        res = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=15)
        if res.returncode != 0 or not res.stdout.strip():
            log_warn("Antigravity CLI is installed, but no active Google account was detected.")
            print("\nPlease open a new terminal window and run:")
            print("    agy auth login")
            print("to authenticate your Google account, then re-run this installer.")
            sys.exit(1)
        log_success("Antigravity authentication verified (Google account active).")
    except subprocess.TimeoutExpired:
        log_warn("Authentication check timed out, proceeding with existing credentials.")
    except Exception as e:
        log_warn(f"Could not verify auth automatically ({e}), proceeding...")


# ─────────────────────────────────────────────────────────────────────────────
# Pip Execution Helper (Auto-supports uv or standard ensurepip)
# ─────────────────────────────────────────────────────────────────────────────

def run_pip(args: list):
    """Executes pip command via uv (if available) or standard python -m pip with auto-ensurepip."""
    uv_bin = shutil.which("uv")
    if uv_bin:
        # Convert pip install args to uv pip syntax
        uv_cmd = [uv_bin, "pip"] + args + ["--python", sys.executable]
        subprocess.check_call(uv_cmd)
        return

    # Fallback to standard pip
    try:
        import pip  # noqa: F401
    except ImportError:
        log_step("Bootstrapping pip via ensurepip...")
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])

    subprocess.check_call([sys.executable, "-m", "pip"] + args)


def ensure_pip_upgraded():
    """Ensures pip inside .venv is modern to prevent wheel name normalization bugs."""
    log_step("Validating pip package manager in virtual environment...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
        log_success("Pip package manager is up to date.")
    except Exception as e:
        log_warn(f"Could not auto-upgrade pip ({e}), proceeding with existing pip...")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Hardware Detection
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 2. Hardware Detection (Pre-PyTorch Physical Probing)
# ─────────────────────────────────────────────────────────────────────────────

def detect_hardware() -> str:
    log_header("STEP 2: HARDWARE DETECTION & TOPOLOGY EVALUATION")
    try:
        from hardware_detector import (
            detect_gpu_profile,
            get_optimal_threads,
            get_cpu_name_and_u_series,
        )
        cpu_name, is_u_series = get_cpu_name_and_u_series()
        threads = get_optimal_threads()
        gpu_info = detect_gpu_profile()

        has_gpu = gpu_info["cuda_capable_gpu"]
        gpu_name = gpu_info["gpu_name"]
        vram_gb = gpu_info["vram_gb"]

        log_step(f"CPU: {cpu_name}")
        log_step(f"Optimal Synthesis Threads: {threads}")
        if has_gpu:
            log_success(f"Dedicated GPU: {gpu_name} ({vram_gb:.1f} GB VRAM) -> Target: CUDA Tensor Core Mode")
            return "cuda"
        else:
            log_step("No qualifying NVIDIA GPU detected (or < 3.75 GB VRAM) -> Target: High-Speed CPU Mode")
            return "cpu"
    except Exception as e:
        log_warn(f"Hardware detection encountered exception: {e}. Defaulting to CPU mode.")
        return "cpu"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Dynamic PyTorch Wheel Resolution
# ─────────────────────────────────────────────────────────────────────────────

def install_pytorch(target_device: str):
    log_header(f"STEP 3: PYTORCH ENGINE VALIDATION ({target_device.upper()})")
    
    # Check if current PyTorch already matches target device
    needs_install = True
    try:
        import torch
        log_step(f"Existing PyTorch version: {torch.__version__}")
        if target_device == "cuda" and torch.cuda.is_available():
            log_success(f"PyTorch CUDA is already active ({torch.cuda.get_device_name(0)}). Skipping re-installation.")
            needs_install = False
        elif target_device == "cpu" and not torch.cuda.is_available():
            log_success("PyTorch CPU is already active. Skipping re-installation.")
            needs_install = False
    except ImportError:
        pass

    if needs_install:
        if target_device == "cuda":
            log_step("Installing PyTorch 2.5.1 with CUDA 12.1 wheel index...")
            run_pip([
                "install",
                "torch==2.5.1", "torchvision==0.20.1", "torchaudio==2.5.1",
                "--index-url", "https://download.pytorch.org/whl/cu121",
                "--extra-index-url", "https://pypi.org/simple"
            ])
        else:
            log_step("Installing PyTorch 2.5.1 with CPU wheel index...")
            run_pip([
                "install",
                "torch==2.5.1", "torchvision==0.20.1", "torchaudio==2.5.1",
                "--index-url", "https://download.pytorch.org/whl/cpu",
                "--extra-index-url", "https://pypi.org/simple"
            ])
        log_success("PyTorch runtime successfully installed.")


def lock_hardware_config():
    """Computes and locks the final hardware_config.json now that PyTorch is installed."""
    try:
        from hardware_detector import get_or_create_hardware_config, save_hardware_config
        config = get_or_create_hardware_config(force_recompute=True)
        if config.get("device") != "cuda":
            config["ja_only"] = True
            save_hardware_config(config)
        log_success(f"Hardware configuration locked: device={config['device']}, threads={config['threads']}, ja_only={config['ja_only']}")
        return config
    except Exception as e:
        log_warn(f"Hardware lock warning ({e}). Continuing with default profile.")
        return {"device": "cpu", "threads": 6, "ja_only": True}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Universal Dependencies (Shielded against PyTorch CUDA overwrite)
# ─────────────────────────────────────────────────────────────────────────────

def install_requirements(target_device: str):
    log_header("STEP 4: INSTALLING RUNTIME DEPENDENCIES")
    req_file = ROOT_DIR / "requirements.txt"
    if not req_file.exists():
        log_error(f"Missing requirements file: {req_file}")
        sys.exit(1)

    log_step(f"Installing dependencies from {req_file.name}...")
    args = ["install", "-r", str(req_file)]
    # Extra index URL prevents pip from downgrading CUDA PyTorch to standard PyPI CPU wheel
    if target_device == "cuda":
        args.extend(["--extra-index-url", "https://download.pytorch.org/whl/cu121"])
    else:
        args.extend(["--extra-index-url", "https://download.pytorch.org/whl/cpu"])

    run_pip(args)
    log_success("All runtime dependencies verified and installed.")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Hugging Face Fine-Tuned Voice Models Download
# ─────────────────────────────────────────────────────────────────────────────

def download_voice_models(repo_id: str = DEFAULT_HF_REPO, target_device: str = "cuda"):
    log_header("STEP 5: FINE-TUNED VOICE MODELS SYNCHRONIZATION")
    log_step(f"Source Repository: https://huggingface.co/{repo_id}")

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface-hub>=0.23.0"])
        from huggingface_hub import hf_hub_download

    # Map remote repo files to target local paths
    model_targets = [
        # Japanese Engine (VN Golden Checkpoint)
        ("japanese/Kurisu_e63_s37500.safetensors", ROOT_DIR / "finalised-japanese-engine" / "model_assets" / "Kurisu_e63_s37500.safetensors"),
        ("japanese/config.json", ROOT_DIR / "finalised-japanese-engine" / "model_assets" / "config.json"),
        ("japanese/style_vectors.npy", ROOT_DIR / "finalised-japanese-engine" / "model_assets" / "style_vectors.npy"),

        # SenseVoice Real-time STT
        ("sensevoice/model.int8.onnx", ROOT_DIR / "models" / "sensevoice" / "model.int8.onnx"),
        ("sensevoice/tokens.txt", ROOT_DIR / "models" / "sensevoice" / "tokens.txt"),
    ]

    # English Engine is only downloaded on CUDA/GPU systems (CPU mode runs in optimized Japanese-only)
    if target_device == "cuda":
        model_targets.extend([
            ("english/Kurisu_EN_e103_s21000.safetensors", ROOT_DIR / "finalised-english-engine" / "Kurisu_EN_e103_s21000.safetensors"),
            ("english/config.json", ROOT_DIR / "finalised-english-engine" / "config.json"),
            ("english/style_vectors.npy", ROOT_DIR / "finalised-english-engine" / "style_vectors.npy"),
        ])
    else:
        log_step("CPU Mode: Skipping English voice model download to save bandwidth and reduce RAM usage.")

    for remote_path, local_path in model_targets:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.is_file() and local_path.stat().st_size > 0:
            log_success(f"Asset verified locally: {local_path.name}")
            continue

        log_step(f"Downloading {remote_path} -> {local_path.name}...")
        try:
            downloaded = hf_hub_download(
                repo_id=repo_id,
                filename=remote_path,
                local_dir=str(ROOT_DIR / "for-hf-cache"),
                local_dir_use_symlinks=False,
            )
            shutil.copy2(downloaded, local_path)
            log_success(f"Downloaded and installed: {local_path.name}")
        except Exception as e:
            log_error(f"Failed to download {remote_path}: {e}")
            print(f"Please check your internet connection or verify the repository {repo_id}.")
            sys.exit(1)

    # Clean up temporary download cache to prevent duplicating ~800 MB on disk
    cache_dir = ROOT_DIR / "for-hf-cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)

    log_success("All neural voice models synchronized successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Sandboxed Profile Setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_sandboxed_profile():
    log_header("STEP 6: SANDBOXED PROFILE INITIALIZATION")
    try:
        from amadeus_brain import ensure_amadeus_profile, get_amadeus_profile_dir
        profile_dir = ensure_amadeus_profile()
        log_success(f"Isolated profile directory configured: {profile_dir}")

        # Ensure external assets directory exists for optional user background audio
        (ROOT_DIR / "assets").mkdir(parents=True, exist_ok=True)

        # Inject Makise Kurisu persona into profile/.gemini/GEMINI.md
        src_persona = ROOT_DIR / "workspace" / "GEMINI.md"
        dst_persona = profile_dir / ".gemini" / "GEMINI.md"
        if src_persona.is_file():
            shutil.copy2(src_persona, dst_persona)
            log_success(f"Kurisu dual-stream persona deployed to {dst_persona}")
        else:
            log_warn(f"Persona template not found at {src_persona}; using existing profile persona.")
    except Exception as e:
        log_warn(f"Profile initialization warning: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Windowless Launcher & Desktop Shortcut Creation
# ─────────────────────────────────────────────────────────────────────────────

def create_windowless_launcher_and_shortcut():
    log_header("STEP 7: WINDOWLESS LAUNCHER & DESKTOP SHORTCUT")

    # 1. Write the silent VBScript launcher (Amadeus.vbs)
    vbs_path = ROOT_DIR / "Amadeus.vbs"
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\n'
        'strPath = fso.GetParentFolderName(WScript.ScriptFullName)\n\n'
        '\' 1. Launch Python backend daemon headlessly using pythonw.exe (0 = hidden window)\n'
        'pythonwExe = strPath & "\\.venv\\Scripts\\pythonw.exe"\n'
        'wsScript = strPath & "\\Codebase\\amadeus_ws.py"\n'
        'WshShell.Run chr(34) & pythonwExe & chr(34) & " " & chr(34) & wsScript & chr(34), 0, False\n\n'
        '\' 2. Brief wait for WebSocket server to bind port 8765\n'
        'WScript.Sleep 1500\n\n'
        '\' 3. Launch Tauri Desktop Client (standalone Amadeus.exe if built, else run_desktop.bat)\n'
        'releaseExe = strPath & "\\desktop\\src-tauri\\target\\release\\Amadeus.exe"\n'
        'If Not fso.FileExists(releaseExe) Then\n'
        '    releaseExe = strPath & "\\Amadeus.exe"\n'
        'End If\n'
        'If fso.FileExists(releaseExe) Then\n'
        '    WshShell.Run chr(34) & releaseExe & chr(34), 1, False\n'
        'Else\n'
        '    WshShell.Run chr(34) & strPath & "\\run_desktop.bat" & chr(34), 1, False\n'
        'End If\n'
    )
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)
    log_success(f"Created windowless launcher: {vbs_path.name}")

    # 2. Create Desktop Shortcut (.lnk) using Windows SpecialFolders resolver
    icon_path = ROOT_DIR / "icon.ico"
    if not icon_path.exists():
        icon_path = ROOT_DIR / "desktop" / "src-tauri" / "icons" / "icon.ico"

    vbs_maker = ROOT_DIR / "_make_shortcut.vbs"
    vbs_maker_code = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        'strDesktop = WshShell.SpecialFolders("Desktop")\n'
        'Set oLink = WshShell.CreateShortcut(strDesktop & "\\Project Amadeus.lnk")\n'
        'oLink.TargetPath = "wscript.exe"\n'
        f'oLink.Arguments = """" & "{vbs_path}" & """"\n'
        f'oLink.WorkingDirectory = "{ROOT_DIR}"\n'
        f'oLink.IconLocation = "{icon_path},0"\n'
        'oLink.Description = "Project Amadeus - Makise Kurisu Desktop AI Workstation"\n'
        'oLink.Save\n'
        'WScript.Echo strDesktop & "\\Project Amadeus.lnk"\n'
    )
    try:
        with open(vbs_maker, "w", encoding="utf-8") as f:
            f.write(vbs_maker_code)
        out = subprocess.check_output(["cscript", "//nologo", str(vbs_maker)], text=True).strip()
        if vbs_maker.exists():
            vbs_maker.unlink()
        log_success(f"Desktop shortcut created: {out}")
    except Exception as e:
        log_warn(f"Could not create desktop shortcut automatically ({e}). You can launch via {vbs_path.name}.")


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("""
    ==============================================================================
            PROJECT AMADEUS: MAKISE KURISU DESKTOP AI WORKSTATION
                          1-Click Automated Installer
    ==============================================================================
    """)

    run_preflight_checks()
    ensure_pip_upgraded()
    target_device = detect_hardware()
    install_pytorch(target_device)
    lock_hardware_config()
    install_requirements(target_device)
    download_voice_models(DEFAULT_HF_REPO, target_device=target_device)
    setup_sandboxed_profile()
    create_windowless_launcher_and_shortcut()

    log_header("INSTALLATION COMPLETE")
    print("""
    [+] Project Amadeus has been installed and configured successfully!
    [+] A shortcut 'Project Amadeus' has been placed on your Desktop.
    
    You can now launch Project Amadeus directly by double-clicking the
    'Project Amadeus' icon on your Desktop!
    """)


if __name__ == "__main__":
    main()
