"""
Project Amadeus: Local Loopback WebSocket Server
Bridges the Python Daemon (Brain, TTS, STT) with the Tauri Desktop Client.
Address: ws://127.0.0.1:8765
"""

import asyncio
import base64
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid
import websockets

# Headless Windows safeguard (pythonw.exe execution)
# When launched via pythonw.exe without a console window, sys.stdout and sys.stderr are None.
# Third-party libraries like loguru (used by Style-Bert-VITS2) crash with TypeError if sys.stdout is None.
if sys.stdout is None:
    try:
        _log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ProjectAmadeus" / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        sys.stdout = open(_log_dir / "amadeus_ws.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")

if sys.stderr is None:
    sys.stderr = sys.stdout if sys.stdout is not None else open(os.devnull, "w", encoding="utf-8")

if sys.stdin is None:
    sys.stdin = open(os.devnull, "r", encoding="utf-8")

# Auto-trampoline into dedicated Python virtual environment if invoked by system Python
_VENV_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv", "Scripts"))
_VENV_PYTHON = os.path.join(_VENV_SCRIPTS, "python.exe")
_VENV_PYTHONW = os.path.join(_VENV_SCRIPTS, "pythonw.exe")

if os.path.isdir(_VENV_SCRIPTS) and __name__ == "__main__":
    current_py = os.path.abspath(sys.executable).lower()
    valid_venv_executables = {os.path.abspath(_VENV_PYTHON).lower(), os.path.abspath(_VENV_PYTHONW).lower()}
    if current_py not in valid_venv_executables:
        target_py = _VENV_PYTHONW if "pythonw" in current_py else _VENV_PYTHON
        if not os.path.isfile(target_py):
            target_py = _VENV_PYTHON
        if os.path.isfile(target_py):
            sys.exit(subprocess.call([target_py, os.path.abspath(__file__)] + sys.argv[1:]))

if __name__ == "__main__":
    sys.modules["amadeus_ws"] = sys.modules[__name__]

# Setup deterministic search paths for Project Amadeus
_CODEBASE_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _CODEBASE_DIR.parent
for _p in [str(_ROOT_DIR), str(_CODEBASE_DIR), str(_ROOT_DIR / "finalised-japanese-engine"), str(_ROOT_DIR / "finalised-english-engine")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_clients: Set[Any] = set()
_lock = threading.Lock()
_ws_loop: Optional[asyncio.AbstractEventLoop] = None
_brain_loop: Optional[asyncio.AbstractEventLoop] = None
_server_thread: Optional[threading.Thread] = None

# Globally enforce CREATE_NO_WINDOW on every process spawned by Amadeus
if sys.platform == "win32":
    _orig_popen_init = subprocess.Popen.__init__
    def _silent_popen_init(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return _orig_popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = _silent_popen_init

# Client liveness and auto-shutdown tracking
_has_client_connected: bool = False
_shutdown_task: Optional[asyncio.Task] = None


def _terminate_daemon_cleanly():
    """Cleanly terminates backend child processes and exits the daemon."""
    global _brain
    try:
        if _brain is not None and getattr(_brain, "_is_running", False):
            if hasattr(_brain, "proc") and _brain.proc:
                try:
                    _brain.proc.terminate()
                except Exception:
                    pass
    except Exception:
        pass
    os._exit(0)


# Subsystem references
_brain: Any = None
_tts: Any = None
_ptt: Any = None
_chat_handler: Optional[Callable[[str], Any]] = None
_voice_enabled: bool = True
_active_subagent_watchers: Dict[str, Any] = {}

try:
    from amadeus_brain import (
        AmadeusBrain,
        query_agy_models,
        get_amadeus_data_dir,
        ensure_trusted_workspace,
        is_folder_trusted,
        verify_model_files,
    )
except ImportError:
    def query_agy_models():
        return [
            {"id": "gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)", "desc": "Gemini 3.8 Flash (Low)"},
            {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)", "desc": "Gemini 3.8 Flash (Medium)"},
            {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)", "desc": "Gemini 3.8 Flash (High)"},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)", "desc": "Claude Sonnet 4.6 (Thinking)"},
            {"id": "claude-opus-4-6-thinking", "name": "Claude Opus 4.6 (Thinking)", "desc": "Claude Opus 4.6 (Thinking)"},
            {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)", "desc": "GPT-OSS 120B (Medium)"},
        ]
    AmadeusBrain = None
    def get_amadeus_data_dir(): return Path.home() / "AppData" / "Local" / "ProjectAmadeus"
    def ensure_trusted_workspace(folder, profile_dir=None): pass
    def is_folder_trusted(folder, profile_dir=None): return True
    def verify_model_files(base_dir=None): return {"status": "ok"}

def get_available_models() -> List[Dict[str, str]]:
    if _brain is not None and hasattr(_brain, "get_available_models"):
        return _brain.get_available_models()
    return query_agy_models()

AVAILABLE_MODELS = query_agy_models()

def get_global_attachments_dir() -> Path:
    """
    Returns the single canonical scratch/attachments directory inside default_workspace:
    %LOCALAPPDATA%\\ProjectAmadeus\\default_workspace\\scratch\\attachments.
    Self-healing: creates default_workspace and scratch/attachments if missing (first run or user deletion).
    """
    att_dir = Path(get_amadeus_data_dir()) / "default_workspace" / "scratch" / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    return att_dir


_CSHARP_FOLDER_PICKER = """
using System;
using System.Runtime.InteropServices;

[ComImport]
[Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")]
public class FileOpenDialogRCW
{
}

[ComImport]
[Guid("42f85136-db7e-439c-85f1-e4075d135fc8")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IFileDialog
{
    [PreserveSig] int Show(IntPtr parent);
    void SetFileTypes();
    void SetFileTypeIndex();
    void GetFileTypeIndex();
    void Advise();
    void Unadvise();
    void SetOptions(uint fos);
    void GetOptions(out uint fos);
    void SetDefaultFolder(IntPtr psi);
    void SetFolder(IntPtr psi);
    void GetFolder(out IntPtr ppsi);
    void GetCurrentSelection(out IntPtr ppsi);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string pszName);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string pszTitle);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string pszText);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string pszLabel);
    void GetResult(out IShellItem ppsi);
    void AddPlace(IntPtr psi, int fdap);
    void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string pszDefaultExtension);
    void Close(int hr);
    void SetClientGuid();
    void ClearClientData();
    void SetFilter();
}

[ComImport]
[Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellItem
{
    void BindToHandler();
    void GetParent();
    void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
    void GetAttributes();
    void Compare();
}

public class AmadeusNativePicker
{
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    public static string ShowFolderDialog(string title)
    {
        var dialog = (IFileDialog)new FileOpenDialogRCW();
        uint options;
        dialog.GetOptions(out options);
        // FOS_PICKFOLDERS = 0x00000020, FOS_FORCEFILESYSTEM = 0x00000040
        dialog.SetOptions(options | 0x00000020 | 0x00000040);
        if (!string.IsNullOrEmpty(title))
        {
            dialog.SetTitle(title);
        }
        dialog.SetOkButtonLabel("Select Folder");
        IntPtr owner = GetForegroundWindow();
        int hr = dialog.Show(owner);
        if (hr == 0)
        {
            IShellItem item;
            dialog.GetResult(out item);
            string path;
            // SIGDN_FILESYSPATH = 0x80058000
            item.GetDisplayName(0x80058000, out path);
            return path;
        }
        return null;
    }
}
"""


def pick_folder_native(title: str = "Select Operational Workspace Directory") -> Optional[str]:
    """
    Displays the authentic modern Windows Explorer Common Open File Dialog (IFileOpenDialog with FOS_PICKFOLDERS).
    - Opens the real Windows Explorer window with Quick Access sidebar, breadcrumb bar, and search box.
    - Bottom button natively displays "Select Folder" (NOT "Upload").
    - 100% bypasses WebView2/Chromium so NO "Upload XX files to this site?" warning ever appears.
    - Attaches to foreground window so it appears modally in front of the active application.
    Returns clean absolute directory path string, or None if user cancelled.
    """
    try:
        clean_title = (title or "Select Folder").replace("'", "''")
        ps_script = "\n".join([
            "Add-Type -TypeDefinition @'",
            _CSHARP_FOLDER_PICKER.strip(),
            "'@",
            f"$folder = [AmadeusNativePicker]::ShowFolderDialog('{clean_title}')",
            "if ($folder) {",
            "    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "    [Console]::WriteLine($folder)",
            "}"
        ])
        encoded_cmd = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-EncodedCommand", encoded_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=300,
        )
        if res.returncode == 0:
            picked = res.stdout.strip()
            if picked and os.path.isdir(picked):
                return str(Path(picked).resolve())
        elif res.stderr:
            print(f"[WS] PowerShell folder picker stderr: {res.stderr.strip()}")
        return None
    except Exception as e:
        print(f"[WS] Error in pick_folder_native: {e}")
        return None


_loading_progress_state: Dict[str, Any] = {
    "step": "Starting Audio Subsystems...",
    "percent": 0,
    "ready_for_workspace": False,
    "error": None
}
_loading_lock = threading.Lock()
_loading_started = False

def _subsystems_loader_worker():
    global _tts, _ptt, _brain, _loading_progress_state
    try:
        # Step 0: Check model files
        _loading_progress_state["step"] = "Verifying model assets..."
        _loading_progress_state["percent"] = 10
        broadcast_sync({"type": "loading_progress", "step": "Verifying model assets...", "percent": 10, "ready_for_workspace": False})
        verify_model_files()

        # Step 1: SenseVoice STT
        _loading_progress_state["step"] = "Loading SenseVoice STT..."
        _loading_progress_state["percent"] = 25
        broadcast_sync({"type": "loading_progress", "step": "Loading SenseVoice STT...", "percent": 25, "ready_for_workspace": False})
        from audio_input import SenseVoiceSTT, AmadeusPushToTalk
        stt = SenseVoiceSTT()
        _ptt = AmadeusPushToTalk(stt)

        # Step 2: SBV2 Japanese Engine e63
        _loading_progress_state["step"] = "Loading SBV2 Japanese Engine e63..."
        _loading_progress_state["percent"] = 50
        broadcast_sync({"type": "loading_progress", "step": "Loading SBV2 Japanese Engine e63...", "percent": 50, "ready_for_workspace": False})
        from tts_engine import AmadeusTTS, SBV2Backend, _JP_ENGINE_DIR
        from amadeus_voice import AmadeusVoiceEngine
        from hardware_detector import get_or_create_hardware_config

        hw_cfg = get_or_create_hardware_config()
        target_device = hw_cfg["device"]
        ja_only = hw_cfg["ja_only"]

        jp_engine = AmadeusVoiceEngine(model_dir=_JP_ENGINE_DIR / "model_assets", device=target_device)

        # Step 3: SBV2 English Engine e103 (skipped if ja_only)
        if not ja_only:
            _loading_progress_state["step"] = "Loading SBV2 English Engine e103..."
            _loading_progress_state["percent"] = 75
            broadcast_sync({"type": "loading_progress", "step": "Loading SBV2 English Engine e103...", "percent": 75, "ready_for_workspace": False})
            from infer import EnglishKurisuVoice
            en_engine = EnglishKurisuVoice(device=target_device)
        else:
            en_engine = None

        # Step 4: INT8 Dynamic DeBERTa (CPU mode only)
        if target_device != "cuda":
            _loading_progress_state["step"] = "Quantizing DeBERTa backbones to INT8..."
            _loading_progress_state["percent"] = 90
            broadcast_sync({"type": "loading_progress", "step": "Quantizing DeBERTa backbones to INT8...", "percent": 90, "ready_for_workspace": False})
            try:
                import torch
                from style_bert_vits2.constants import Languages
                from style_bert_vits2.nlp import bert_models
                loaded = getattr(bert_models, "__loaded_models", {})
                langs_to_quant = (Languages.JP,) if ja_only else (Languages.JP, Languages.EN)
                for lang_key in langs_to_quant:
                    if lang_key in loaded and loaded[lang_key] is not None:
                        loaded[lang_key] = torch.ao.quantization.quantize_dynamic(
                            loaded[lang_key], {torch.nn.Linear}, dtype=torch.qint8
                        )
            except Exception as q_err:
                print(f"[WS] INT8 quantization notice: {q_err}")
        else:
            _loading_progress_state["step"] = "Configuring CUDA Tensor Core acceleration..."
            _loading_progress_state["percent"] = 90
            broadcast_sync({"type": "loading_progress", "step": "Configuring CUDA Tensor Core acceleration...", "percent": 90, "ready_for_workspace": False})

        tts = AmadeusTTS()
        backend = SBV2Backend(device=target_device)
        backend.jp_engine = jp_engine
        backend.en_engine = en_engine
        try:
            backend.synthesize("準備完了。", {"Neutral": 1.0}, lang="JA")
            if not ja_only:
                backend.synthesize("System ready.", {"Neutral": 1.0}, lang="EN")
        except Exception:
            pass
        tts._backend = backend
        tts.initialize()
        _tts = tts
        if hasattr(_tts, "set_muted"):
            _tts.set_muted(not _voice_enabled)

        if _brain is None and AmadeusBrain is not None:
            _brain = AmadeusBrain(model="gemini-3.8-flash-low")

        _loading_progress_state["step"] = "Audio Subsystems Ready."
        _loading_progress_state["percent"] = 100
        _loading_progress_state["ready_for_workspace"] = True
        _loading_progress_state["error"] = None
        broadcast_sync({"type": "loading_progress", "step": "Audio Subsystems Ready.", "percent": 100, "ready_for_workspace": True})
    except Exception as err:
        err_msg = str(err)
        err_payload = {
            "type": "loading_error",
            "stage": "models",
            "error": err_msg,
            "details": f"Failed during subsystem initialization: {err_msg}"
        }
        _loading_progress_state["error"] = err_payload
        _loading_progress_state["ready_for_workspace"] = False
        print(f"[WS] Subsystem loading error: {err}")
        import traceback
        traceback.print_exc()
        broadcast_sync(err_payload)

def start_subsystems_loading(force: bool = False):
    global _loading_started
    with _loading_lock:
        if _loading_started and not force:
            return
        _loading_started = True
        _loading_progress_state["error"] = None
        _loading_progress_state["ready_for_workspace"] = False
        t = threading.Thread(target=_subsystems_loader_worker, daemon=True, name="AmadeusSubsystemsLoader")
        t.start()



def get_image_dimensions_fast(file_bytes: bytes) -> Tuple[int, int]:
    """
    Sub-millisecond pure-Python image header reader that extracts (width, height)
    directly from raw header bytes without decoding the full bitmap into memory.
    Supports PNG, GIF, BMP, WebP, and JPEG.
    Fallback: (1920, 1080).
    """
    if not file_bytes or len(file_bytes) < 10:
        return (1920, 1080)

    try:
        # PNG: bytes 16-24 contain struct.unpack('>II', file_bytes[16:24])
        if file_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(file_bytes) >= 24:
            w, h = struct.unpack(">II", file_bytes[16:24])
            if w > 0 and h > 0:
                return (w, h)

        # GIF: bytes 6-10 contain struct.unpack('<HH', file_bytes[6:10])
        if file_bytes.startswith((b"GIF87a", b"GIF89a")) and len(file_bytes) >= 10:
            w, h = struct.unpack("<HH", file_bytes[6:10])
            if w > 0 and h > 0:
                return (w, h)

        # BMP: bytes 18-26 contain struct.unpack('<II', file_bytes[18:26])
        if file_bytes.startswith(b"BM") and len(file_bytes) >= 26:
            w, h = struct.unpack("<II", file_bytes[18:26])
            h = abs(int(h))
            if w > 0 and h > 0:
                return (w, h)

        # WebP: parse VP8 (bytes 26-30), VP8L (bytes 21-25), or VP8X (bytes 24-30)
        if file_bytes.startswith(b"RIFF") and len(file_bytes) >= 30 and file_bytes[8:12] == b"WEBP":
            chunk = file_bytes[12:16]
            if chunk == b"VP8 " and len(file_bytes) >= 30:
                if file_bytes[23:26] == b"\x9d\x01\x2a":
                    w = struct.unpack("<H", file_bytes[26:28])[0] & 0x3FFF
                    h = struct.unpack("<H", file_bytes[28:30])[0] & 0x3FFF
                    if w > 0 and h > 0:
                        return (w, h)
            elif chunk == b"VP8L" and len(file_bytes) >= 25:
                if file_bytes[20] == 0x2F:
                    b = struct.unpack("<I", file_bytes[21:25])[0]
                    w = 1 + (b & 0x3FFF)
                    h = 1 + ((b >> 14) & 0x3FFF)
                    if w > 0 and h > 0:
                        return (w, h)
            elif chunk == b"VP8X" and len(file_bytes) >= 30:
                w = 1 + struct.unpack("<I", file_bytes[24:27] + b"\x00")[0]
                h = 1 + struct.unpack("<I", file_bytes[27:30] + b"\x00")[0]
                if w > 0 and h > 0:
                    return (w, h)

        # JPEG: parse markers until SOF0/SOF2 (0xFF, 0xC0 / 0xFF, 0xC2), extract height and width
        if file_bytes.startswith(b"\xFF\xD8"):
            offset = 2
            size = len(file_bytes)
            while offset < size - 8:
                if file_bytes[offset] != 0xFF:
                    offset += 1
                    continue
                while offset < size and file_bytes[offset] == 0xFF:
                    offset += 1
                if offset >= size:
                    break
                marker = file_bytes[offset]
                offset += 1
                if marker in (0xDA, 0xD9):  # SOS or EOI
                    break
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if offset + 7 <= size:
                        h, w = struct.unpack(">HH", file_bytes[offset + 3 : offset + 7])
                        if w > 0 and h > 0:
                            return (w, h)
                    break
                if offset + 2 <= size:
                    segment_len = struct.unpack(">H", file_bytes[offset : offset + 2])[0]
                    offset += segment_len
                else:
                    break
    except Exception:
        pass

    return (1920, 1080)


def get_media_duration_sec(file_path: Path, file_bytes: bytes, is_video: bool = False) -> float:
    """Extracts or estimates duration in seconds for audio and video media."""
    try:
        import mutagen
        mf = mutagen.File(str(file_path))
        if mf is not None and hasattr(mf, "info") and hasattr(mf.info, "length") and mf.info.length:
            return float(mf.info.length)
    except Exception:
        pass

    try:
        import wave
        with wave.open(str(file_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return float(frames) / float(rate)
    except Exception:
        pass

    # Heuristic fallback based on typical bitrates
    if is_video:
        return max(1.0, len(file_bytes) / (125 * 1024))
    else:
        return max(1.0, len(file_bytes) / (16 * 1024))



def attach_subsystems(
    brain: Any = None,
    tts: Any = None,
    chat_handler: Optional[Callable[[str], Any]] = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
    ptt: Any = None,
):
    """Binds active Brain, TTS, PTT/STT, and the Brain's owning event loop to the WebSocket bridge."""
    global _brain, _tts, _chat_handler, _brain_loop, _ptt
    if brain is not None:
        _brain = brain
        if hasattr(_brain, "on_conversation_updated"):
            _brain.on_conversation_updated = lambda: broadcast_sync({"type": "convos_updated", "conversations": _brain.list_conversations()})
    if tts is not None:
        _tts = tts
    if chat_handler is not None:
        _chat_handler = chat_handler
    if loop is not None:
        _brain_loop = loop
    else:
        try:
            _brain_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
    if ptt is not None:
        _ptt = ptt


def dispatch_brain_coroutine(coro: Any):
    """
    Universal Coroutine Dispatcher:
    - If running in multi-threaded terminal mode (_brain_loop is active),
      schedules thread-safely to _brain_loop.
    - Otherwise (standalone desktop daemon mode on _ws_loop),
      schedules directly as an asyncio.Task on the active event loop.
    Guarantees no command handler can ever drop a coroutine again.
    """
    if _brain_loop is not None and _brain_loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, _brain_loop)
    return asyncio.create_task(coro)


def _get_or_create_ptt():
    global _ptt
    if _ptt is not None:
        return _ptt
    try:
        from audio_input import SenseVoiceSTT, AmadeusPushToTalk
        stt = SenseVoiceSTT()
        _ptt = AmadeusPushToTalk(stt)
        return _ptt
    except Exception as e:
        print(f"[WS] Warning: Failed to initialize PTT engine: {e}")
        return None


def list_workspace_files(cwd: Optional[str]) -> List[str]:
    """
    Scans cwd recursively for files and subfolders, excluding common build/dependency/hidden directories.
    Returns relative paths with forward slashes, sorted alphabetically, capped at 1000 items.
    Subfolders end with a trailing '/'.
    """
    if not cwd or not os.path.isdir(cwd):
        return []

    excluded_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
        "build", "target", "scratch", ".system_generated", ".next", ".cache"
    }

    items: List[str] = []
    root_path = Path(cwd).resolve()

    try:
        for root, dirs, filenames in os.walk(root_path):
            # Exclude hidden directories and listed excluded directories in-place
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in excluded_dirs
            ]
            # Include subdirectories with trailing slash
            for d in dirs:
                full_dir = Path(root) / d
                try:
                    rel_dir = full_dir.relative_to(root_path).as_posix() + "/"
                    items.append(rel_dir)
                    if len(items) >= 1000:
                        break
                except Exception:
                    continue

            if len(items) >= 1000:
                break

            for fname in filenames:
                if fname.startswith("."):
                    continue
                full_path = Path(root) / fname
                try:
                    rel_path = full_path.relative_to(root_path).as_posix()
                    items.append(rel_path)
                    if len(items) >= 1000:
                        break
                except Exception:
                    continue
            if len(items) >= 1000:
                break
    except Exception as e:
        print(f"[WS] Error listing workspace files: {e}")

    items.sort(key=lambda s: s.lower())
    return items


def extract_mentioned_files(text: str, cwd: Optional[str]) -> List[str]:
    """
    Extracts candidate @<token> tokens and verifies if each candidate exists
    in the workspace directory cwd (as a file or directory). Returns a list of resolved absolute paths.
    Supports filenames with @ symbols (e.g. @contact@gmail, @logo@2x.png).
    Does NOT remove or modify @mentions in text.
    """
    if not text or not cwd:
        return []
    base_cwd = Path(cwd)
    candidates = re.findall(r'@([A-Za-z0-9_\-./\\@]+)', text)
    seen_paths = set()
    mentioned_files: List[str] = []
    for cand in candidates:
        cand_clean = cand.rstrip("/\\")
        cand_path = base_cwd / cand_clean
        try:
            if cand_path.exists():
                resolved_str = str(cand_path.resolve())
                if resolved_str not in seen_paths:
                    seen_paths.add(resolved_str)
                    mentioned_files.append(resolved_str)
        except Exception:
            pass
    return mentioned_files


_ROOT_DIR = Path(__file__).resolve().parent.parent


def get_avatar_config() -> Dict[str, Any]:
    avatar_dir = _ROOT_DIR / "avatar"
    sprites_dir = avatar_dir / "sprites"
    manifest_file = avatar_dir / "manifest.json"

    # Fallback to desktop/public/avatar if root/avatar is not yet present
    if not sprites_dir.exists():
        fallback_dir = _ROOT_DIR / "desktop" / "public" / "avatar"
        if (fallback_dir / "sprites").exists():
            avatar_dir = fallback_dir
            sprites_dir = fallback_dir / "sprites"
            manifest_file = fallback_dir / "manifest.json"

    sprites_exist = False
    if sprites_dir.exists():
        sprites_exist = any(sprites_dir.glob("*.png"))

    manifest_data = None
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
        except Exception as e:
            print(f"[WS] Warning: Failed to parse avatar manifest.json: {e}")

    return {
        "avatar_dir": str(avatar_dir),
        "sprites_dir": str(sprites_dir),
        "sprites_available": sprites_exist,
        "manifest": manifest_data,
    }


def get_init_payload() -> Dict[str, Any]:
    active_model = _brain.model if _brain and hasattr(_brain, "model") else "gemini-3.8-flash-low"
    convos = _brain.list_conversations() if _brain and hasattr(_brain, "list_conversations") else []
    models = _brain.get_available_models() if _brain and hasattr(_brain, "get_available_models") else AVAILABLE_MODELS

    active_conv_id = _brain.conversation_id if _brain and hasattr(_brain, "conversation_id") and _brain.conversation_id else None
    if not active_conv_id and convos:
        active_conv_id = convos[0].get("id")
        if _brain and hasattr(_brain, "conversation_id"):
            _brain.conversation_id = active_conv_id

    history = []
    active_title = ""
    if active_conv_id and _brain and hasattr(_brain, "get_conversation_history"):
        try:
            history = _brain.get_conversation_history(active_conv_id)
        except Exception:
            history = []
        for c in convos:
            if c.get("id") == active_conv_id:
                active_title = c.get("title", "")
                break

    cwd = str(_brain.cwd) if _brain and hasattr(_brain, "cwd") and _brain.cwd else None
    default_ws = str(Path(get_amadeus_data_dir()) / "default_workspace")
    is_default = bool(cwd and os.path.normcase(os.path.abspath(cwd)) == os.path.normcase(os.path.abspath(default_ws)))
    workspace_files = list_workspace_files(cwd) if cwd else []

    return {
        "type": "init_state",
        "model": active_model,
        "models": models,
        "conversations": convos,
        "voice_enabled": _voice_enabled,
        "active_conversation_id": active_conv_id,
        "active_conversation_title": active_title,
        "history": history,
        "cwd": cwd,
        "is_default": is_default,
        "workspace_files": workspace_files,
        "avatar_config": get_avatar_config(),
    }


async def _watch_subagent_transcript(subagent_id: str, conv_id: Optional[str]):
    """
    Background polling coroutine for real-time subagent step streaming.
    Polls transcript log file size every 800ms and broadcasts subagent_info deltas.
    Exits cleanly and notifies when terminal state is reached.
    """
    try:
        if _brain and hasattr(_brain, "profile_dir"):
            profile_dir = Path(_brain.profile_dir)
        else:
            profile_dir = Path.home()

        log_dir = profile_dir / ".gemini" / "antigravity-cli" / "brain" / subagent_id / ".system_generated" / "logs"
        full_log = log_dir / "transcript_full.jsonl"
        compact_log = log_dir / "transcript.jsonl"

        last_size = -1
        terminal_states = {"DONE", "KILLED", "ERROR"}

        while True:
            await asyncio.sleep(0.8)

            target_file = None
            if full_log.is_file():
                target_file = full_log
            elif compact_log.is_file():
                target_file = compact_log

            current_size = -1
            if target_file is not None:
                try:
                    current_size = target_file.stat().st_size
                except Exception:
                    current_size = -1

            if current_size != last_size and current_size >= 0:
                last_size = current_size
                if _brain and hasattr(_brain, "get_subagent_info"):
                    try:
                        info = _brain.get_subagent_info(conv_id, subagent_id)
                        if info:
                            if hasattr(_brain, "_resolve_subagent_state"):
                                info["state"] = _brain._resolve_subagent_state(subagent_id, parent_cid=conv_id)
                            broadcast_sync({"type": "subagent_info", "subagent_id": subagent_id, "data": info})
                    except Exception as e:
                        print(f"[WS] Error reading subagent info: {e}")

            # Check subagent state
            sub_state = "ACTIVE"
            if _brain and hasattr(_brain, "_resolve_subagent_state"):
                try:
                    sub_state = _brain._resolve_subagent_state(subagent_id, parent_cid=conv_id)
                except Exception:
                    sub_state = "ACTIVE"

            if sub_state in terminal_states:
                # Wait 1 tick (800ms) for final logs flush
                await asyncio.sleep(0.8)

                if _brain and hasattr(_brain, "get_subagent_info"):
                    try:
                        final_info = _brain.get_subagent_info(conv_id, subagent_id)
                        if final_info:
                            final_info["state"] = sub_state
                            broadcast_sync({"type": "subagent_info", "subagent_id": subagent_id, "data": final_info})
                    except Exception:
                        pass

                if _brain and hasattr(_brain, "get_conversation_subagents"):
                    try:
                        subagents = _brain.get_conversation_subagents(conv_id)
                        broadcast_sync({"type": "session_subagents", "subagents": subagents})
                    except Exception:
                        pass

                break

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[WS] Exception in _watch_subagent_transcript for {subagent_id}: {e}")
    finally:
        _active_subagent_watchers.pop(subagent_id, None)


def _start_subagent_watcher(subagent_id: str, conv_id: Optional[str] = None):
    """
    Safely starts a subagent transcript watcher task on the running event loop (_brain_loop or _ws_loop).
    Prevents duplicate watcher tasks for the same subagent_id.
    """
    if not subagent_id:
        return
    existing = _active_subagent_watchers.get(subagent_id)
    if existing and not existing.done():
        return

    effective_cid = conv_id or (_brain.conversation_id if _brain else None)
    target_loop = _brain_loop if (_brain_loop and _brain_loop.is_running()) else _ws_loop
    if not target_loop or not target_loop.is_running():
        try:
            target_loop = asyncio.get_running_loop()
        except RuntimeError:
            return

    try:
        curr_loop = asyncio.get_running_loop()
    except RuntimeError:
        curr_loop = None

    if curr_loop is target_loop:
        task = target_loop.create_task(_watch_subagent_transcript(subagent_id, effective_cid))
        _active_subagent_watchers[subagent_id] = task
    else:
        def _spawn():
            cur = _active_subagent_watchers.get(subagent_id)
            if not cur or cur.done():
                t = target_loop.create_task(_watch_subagent_transcript(subagent_id, effective_cid))
                _active_subagent_watchers[subagent_id] = t

        target_loop.call_soon_threadsafe(_spawn)


async def _process_incoming_command(websocket, data: Dict[str, Any]):
    global _voice_enabled, _brain
    cmd_type = data.get("type")

    # 1. Desktop GUI requests initialization data
    if cmd_type == "request_init":
        await websocket.send(json.dumps(get_init_payload()))
        return

    # 1b. Loading progress query & retry
    if cmd_type == "request_loading_status":
        if _loading_progress_state.get("error"):
            await websocket.send(json.dumps(_loading_progress_state["error"]))
        else:
            await websocket.send(json.dumps({
                "type": "loading_progress",
                "step": _loading_progress_state.get("step", "Audio Subsystems Ready."),
                "percent": _loading_progress_state.get("percent", 100),
                "ready_for_workspace": _loading_progress_state.get("ready_for_workspace", True)
            }))
        return

    if cmd_type == "retry_loading":
        start_subsystems_loading(force=True)
        return

    # 1c. Workspace Onboarding Protocol
    if cmd_type == "pick_folder":
        folder = await asyncio.to_thread(pick_folder_native)
        await websocket.send(json.dumps({
            "type": "folder_picked",
            "folder": folder
        }))
        return

    if cmd_type == "check_folder_trust":
        folder = data.get("folder", "")
        prof_dir = str(_brain.profile_dir) if (_brain and hasattr(_brain, "profile_dir")) else None
        trusted = is_folder_trusted(folder, profile_dir=prof_dir)
        await websocket.send(json.dumps({
            "type": "trust_status",
            "trusted": bool(trusted),
            "folder": folder
        }))
        return

    if cmd_type == "trust_folder":
        folder = data.get("folder", "")
        prof_dir = str(_brain.profile_dir) if (_brain and hasattr(_brain, "profile_dir")) else None
        ensure_trusted_workspace(folder, profile_dir=prof_dir)
        await websocket.send(json.dumps({
            "type": "trust_status",
            "trusted": True,
            "folder": folder
        }))
        return

    if cmd_type == "check_gemini_conflict":
        folder = data.get("folder", "")
        has_conflict = False
        if folder and os.path.isdir(folder):
            has_conflict = os.path.isfile(os.path.join(folder, "GEMINI.md"))
        await websocket.send(json.dumps({
            "type": "gemini_status",
            "has_conflict": bool(has_conflict),
            "folder": folder
        }))
        return

    if cmd_type == "start_workspace":
        workspace_path = data.get("workspace_path")
        is_default = bool(data.get("is_default", False))
        try:
            if is_default or not workspace_path:
                target_path = Path(get_amadeus_data_dir()) / "default_workspace"
                target_path.mkdir(parents=True, exist_ok=True)
                # Self-heal canonical global attachments directory inside default_workspace
                get_global_attachments_dir()
            else:
                target_path = Path(workspace_path)

            target_path = target_path.resolve()

            if _brain is None:
                _brain = AmadeusBrain(model="gemini-3.8-flash-low", cwd=str(target_path))
            else:
                _brain.cwd = str(target_path)
                if getattr(_brain, "_is_running", False):
                    await _brain.close()

            await _brain.start()
            broadcast_sync({"type": "workspace_ready", "cwd": str(target_path)})
            broadcast_sync({"type": "workspace_files", "files": list_workspace_files(str(target_path))})
        except Exception as e:
            print(f"[WS] Error starting workspace: {e}")
            broadcast_sync({"type": "workspace_error", "error": str(e)})
        return

    if cmd_type == "close_workspace":
        if _brain is not None and getattr(_brain, "_is_running", False):
            await _brain.close()
        # CRITICAL: Do NOT close or terminate _tts, _ptt, or local model weights! They stay hot in RAM!
        broadcast_sync({"type": "workspace_closed"})
        broadcast_sync({"type": "workspace_files", "files": []})
        return

    # 2. Window focus changed (Sentence-boundary language routing)
    if cmd_type == "window_focus":
        focused = bool(data.get("focused", True))
        if _tts is not None and hasattr(_tts, "set_gui_focused"):
            _tts.set_gui_focused(focused)
        try:
            import tts_engine
            tts_engine.set_gui_focused(focused)
        except Exception:
            pass
        return

    # 3. Desktop Mic Recording (Click to record -> Click to stop -> Paste to textarea)
    if cmd_type == "start_mic_record":
        ptt = _get_or_create_ptt()
        if ptt is not None and hasattr(ptt, "start_recording"):
            ptt.start_recording()
            broadcast_sync({"type": "mic_recording_state", "recording": True})
        return

    if cmd_type == "stop_mic_record":
        ptt = _get_or_create_ptt()
        clean_text = ""
        if ptt is not None and hasattr(ptt, "stop_recording"):
            res = ptt.stop_recording()
            clean_text = res.get("clean_text") or res.get("text", "")
        await websocket.send(json.dumps({"type": "mic_transcript", "text": clean_text}))
        broadcast_sync({"type": "mic_recording_state", "recording": False})
        return

    # 3b. Stage attachment
    if cmd_type == "stage_attachment":
        name = data.get("name", "attachment")
        mime_type = data.get("mime_type", "")
        is_img = data.get("is_image", False)
        b64_data = data.get("base64_data", "")
        att_id = data.get("id", "")
        size = data.get("size", 0)

        scratch_attachments_dir = get_global_attachments_dir()

        file_bytes = base64.b64decode(b64_data) if b64_data else b""
        ext = Path(name).suffix
        if not ext:
            guessed = mimetypes.guess_extension(mime_type) if mime_type else None
            ext = guessed if guessed else (".png" if is_img else ".bin")

        safe_name = f"{uuid.uuid4().hex[:8]}_{Path(name).stem}{ext}"
        file_path = scratch_attachments_dir / safe_name
        with open(file_path, "wb") as f:
            f.write(file_bytes)
        file_uri = file_path.resolve().as_uri()

        # Precision Token Engine
        tokens = 0
        ext_lower = ext.lower()
        if is_img or ext_lower in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            is_img = True
            w, h = get_image_dimensions_fast(file_bytes)
            if w <= 384 and h <= 384:
                tokens = 258
            else:
                tiles = math.ceil(w / 768) * math.ceil(h / 768)
                tokens = tiles * 258
        elif ext_lower in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"):
            dur = get_media_duration_sec(file_path, file_bytes, is_video=False)
            tokens = math.ceil(dur * 32)
        elif ext_lower in (".mp4", ".webm", ".mkv", ".avi", ".mov", ".wmv"):
            dur = get_media_duration_sec(file_path, file_bytes, is_video=True)
            tokens = math.ceil(dur * 263)
        elif ext_lower == ".pdf":
            try:
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                raw_page_matches = re.findall(rb"/Type\s*/Page\b", file_bytes)
                page_count = max(1, len(raw_page_matches))
                extracted_text = ""
                try:
                    import pypdf
                    reader = pypdf.PdfReader(str(file_path))
                    if len(reader.pages) > 0:
                        page_count = len(reader.pages)
                    pages_text = [page.extract_text() or "" for page in reader.pages]
                    extracted_text = " ".join(pages_text).strip()
                except Exception:
                    pass

                if len(extracted_text) <= 50:
                    bt_matches = re.findall(rb"BT\s*(.*?)\s*ET", file_bytes, re.DOTALL)
                    stream_chunks = []
                    for b in bt_matches:
                        chars = re.findall(rb"[\x20-\x7e\n\r\t]{4,}", b)
                        stream_chunks.extend(chars)
                    raw_stream_text = b" ".join(stream_chunks).decode("ascii", errors="replace").strip()
                    if len(raw_stream_text) > 50:
                        extracted_text = raw_stream_text

                if len(extracted_text) > 50:
                    tokens = len(enc.encode(extracted_text))
                else:
                    tokens = page_count * 258
            except Exception:
                raw_page_matches = re.findall(rb"/Type\s*/Page\b", file_bytes)
                tokens = max(1, len(raw_page_matches)) * 258
        elif ext_lower in (".docx", ".pptx", ".xlsx"):
            try:
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                import zipfile, xml.etree.ElementTree as ET
                with zipfile.ZipFile(str(file_path)) as z:
                    xml_names = [n for n in z.namelist() if n.endswith(".xml")]
                    texts = []
                    for xn in xml_names:
                        xml_bytes = z.read(xn)
                        tree = ET.fromstring(xml_bytes)
                        texts.append("".join(tree.itertext()))
                    extracted = " ".join(texts)
                    tokens = len(enc.encode(extracted)) if extracted.strip() else max(1, math.ceil((len(file_bytes) / 1024) * 2.5))
            except Exception:
                strings = re.findall(rb"[\x20-\x7e\n\r\t]{4,}", file_bytes)
                text_content = b" ".join(strings).decode("ascii", errors="replace")
                try:
                    import tiktoken
                    enc = tiktoken.get_encoding("cl100k_base")
                    tokens = len(enc.encode(text_content)) if text_content.strip() else max(1, math.ceil((len(file_bytes) / 1024) * 2.5))
                except Exception:
                    tokens = max(1, math.ceil((len(file_bytes) / 1024) * 2.5))
        else:
            try:
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                text_content = file_bytes.decode("utf-8", errors="replace")
                tokens = len(enc.encode(text_content))
            except Exception:
                tokens = max(1, math.ceil((len(file_bytes) / 1024) * 2.5))

        staged_packet = {
            "type": "attachment_staged",
            "id": att_id,
            "name": name,
            "path": str(file_path),
            "file_uri": file_uri,
            "tokens": tokens,
            "size": len(file_bytes),
            "is_image": is_img
        }
        if is_img:
            staged_packet["data_url"] = f"data:{mime_type or 'image/png'};base64,{b64_data}"

        await websocket.send(json.dumps(staged_packet))
        return

    # 3c. Unstage attachment
    if cmd_type == "unstage_attachment":
        path_str = data.get("path", "")
        if path_str:
            try:
                norm_path = Path(path_str).resolve()
                scratch_dir = get_global_attachments_dir().resolve()
                if scratch_dir in norm_path.parents or norm_path == scratch_dir:
                    if norm_path.is_file():
                        norm_path.unlink()
            except Exception as e:
                print(f"[WS] Error unstaging attachment: {e}")
        return

    # 3d. Get image data URL for safe local display (ss-16.png bug fix)
    if cmd_type == "get_image_data_url":
        path_str = data.get("path", "")
        req_id = data.get("req_id", "")
        if path_str:
            try:
                raw_target = path_str
                if raw_target.startswith("file:///"):
                    raw_target = raw_target[8:]
                elif raw_target.startswith("file://"):
                    raw_target = raw_target[7:]
                if raw_target.startswith("/") and len(raw_target) > 2 and raw_target[2] == ":":
                    raw_target = raw_target[1:]
                from urllib.parse import unquote
                raw_target = unquote(raw_target)
                clean_path = Path(raw_target).resolve()
                if clean_path.is_file():
                    with open(clean_path, "rb") as f:
                        ibytes = f.read()
                    guessed_mime, _ = mimetypes.guess_type(str(clean_path))
                    mime = guessed_mime or "image/png"
                    b64 = base64.b64encode(ibytes).decode("ascii")
                    await websocket.send(json.dumps({
                        "type": "image_data_url_response",
                        "path": str(clean_path),
                        "raw_path": path_str,
                        "req_id": req_id,
                        "data_url": f"data:{mime};base64,{b64}"
                    }))
            except Exception as e:
                print(f"[WS] Error reading image data url for {path_str}: {e}")
        return


    # 4. Desktop GUI sends a user chat message
    if cmd_type in ("user_chat", "user_message"):
        text = data.get("text", "").strip()
        staged_paths = data.get("staged_paths", [])

        # Mandatory text prompt check: if text is empty, ignore
        if not text:
            return

        if staged_paths:
            attachment_lines = []
            for item in staged_paths:
                att_name = item.get("name", "attachment")
                file_uri = item.get("file_uri", "")
                if not file_uri and item.get("path"):
                    file_uri = Path(item["path"]).resolve().as_uri()
                is_img = item.get("is_image", False)
                if is_img:
                    attachment_lines.append(f"![{att_name}]({file_uri})")
                else:
                    attachment_lines.append(f"[{att_name}]({file_uri})")
            if attachment_lines:
                attachments_md = "\n".join(attachment_lines)
                text = f"{attachments_md}\n\n{text}".strip()

        # Handle slash commands directly from chat input
        if text.lower() == "/clear":
            async def _do_reset():
                if _tts is not None and hasattr(_tts, "stop"):
                    _tts.stop()
                if _brain is not None and hasattr(_brain, "reset_session"):
                    await _brain.reset_session()
                broadcast_sync({"type": "chat_reset"})
                print(f"\n[*] New Chat started via /clear")

            dispatch_brain_coroutine(_do_reset())
            return

        if text.lower() == "/diff":
            diffs = _brain.get_conversation_diffs() if _brain and hasattr(_brain, "get_conversation_diffs") else []
            broadcast_sync({"type": "open_detailed_view", "view": "diffs", "diffs": diffs})
            return

        if text.lower() == "/tasks":
            tasks = _brain.get_conversation_tasks() if _brain and hasattr(_brain, "get_conversation_tasks") else []
            broadcast_sync({"type": "open_detailed_view", "view": "tasks", "tasks": tasks})
            return

        if text.lower() == "/subagents":
            subagents = _brain.get_conversation_subagents() if _brain and hasattr(_brain, "get_conversation_subagents") else []
            broadcast_sync({"type": "open_detailed_view", "view": "subagents", "subagents": subagents})
            return

        if text.lower() == "/commands":
            commands = _brain.get_conversation_commands() if _brain and hasattr(_brain, "get_conversation_commands") else []
            broadcast_sync({"type": "open_detailed_view", "view": "terminal", "commands": commands})
            return

        cwd = str(_brain.cwd) if _brain and hasattr(_brain, "cwd") and _brain.cwd else None
        mentioned_files = extract_mentioned_files(text, cwd)

        if _brain and hasattr(_brain, "send_prompt"):
            try:
                _brain.send_prompt(text)
            except Exception as e:
                print(f"[WS] Error calling _brain.send_prompt: {e}")

        # Echo user message to desktop
        broadcast_sync({"type": "user_msg", "text": text, "mentioned_files": mentioned_files})
        try:
            await websocket.send(json.dumps({
                "type": "user_message_ack",
                "text": text,
                "mentioned_files": mentioned_files
            }))
        except Exception:
            pass

        if _brain is not None:
            dispatch_brain_coroutine(_run_brain_chat(text, mentioned_files=mentioned_files))
        else:
            asyncio.create_task(_mock_chat_response(text))
        return

    # 5. Cognitive model switch
    if cmd_type == "switch_model":
        model_id = data.get("model")
        if model_id and _brain is not None and hasattr(_brain, "switch_model"):
            async def _do_switch():
                try:
                    await _brain.switch_model(model_id)
                    broadcast_sync({"type": "model_switched", "model": model_id})
                    print(f"\n[*] Cognitive model switched via Desktop: {model_id}")
                except Exception as e:
                    print(f"[WS] Error switching model: {e}")

            dispatch_brain_coroutine(_do_switch())
        return

    # 6. New chat session
    if cmd_type == "new_chat":
        async def _do_reset():
            if _tts is not None and hasattr(_tts, "stop"):
                _tts.stop()
            if _brain is not None and hasattr(_brain, "reset_session"):
                await _brain.reset_session()
            broadcast_sync({"type": "chat_reset"})
            print(f"\n[*] New Chat started via Desktop")

        dispatch_brain_coroutine(_do_reset())
        return

    # 7. Resume prior conversation from archive
    if cmd_type == "resume_convo":
        convo_id = data.get("id")
        if convo_id and _brain is not None and hasattr(_brain, "switch_conversation"):
            async def _do_resume():
                try:
                    await _brain.switch_conversation(convo_id)
                    history = _brain.get_conversation_history(convo_id) if hasattr(_brain, "get_conversation_history") else []
                    broadcast_sync({"type": "convo_resumed", "id": convo_id, "history": history})
                    if hasattr(_brain, "model") and _brain.model:
                        broadcast_sync({"type": "model_switched", "model": _brain.model})
                    print(f"\n[*] Conversation resumed via Desktop: {convo_id} (model: {_brain.model})")
                except Exception as e:
                    print(f"[WS] Error resuming convo: {e}")

            dispatch_brain_coroutine(_do_resume())
        return

    # 8. Rename conversation
    if cmd_type == "rename_convo":
        conv_id = data.get("id")
        new_title = data.get("title", "").strip()
        if conv_id and new_title and _brain is not None and hasattr(_brain, "rename_conversation"):
            _brain.rename_conversation(conv_id, new_title)
            broadcast_sync({"type": "convos_updated", "conversations": _brain.list_conversations()})
        return

    # 9. Delete conversation
    if cmd_type == "delete_convo":
        conv_id = data.get("id")
        if conv_id and _brain is not None and hasattr(_brain, "delete_conversation"):
            async def _do_delete():
                await _brain.delete_conversation(conv_id)
                broadcast_sync({"type": "convos_updated", "conversations": _brain.list_conversations()})
                if _brain.conversation_id is None:
                    broadcast_sync({"type": "chat_reset"})

            dispatch_brain_coroutine(_do_delete())
        return

    # 10. Toggle voice speech audio
    if cmd_type == "toggle_voice":
        _voice_enabled = bool(data.get("enabled", not _voice_enabled))
        if _tts is not None and hasattr(_tts, "set_muted"):
            _tts.set_muted(not _voice_enabled)
        broadcast_sync({"type": "voice_toggled", "enabled": _voice_enabled})
        return

    # 11. Halt live streaming generation
    if cmd_type == "stop_generation":
        async def _do_stop():
            try:
                if _tts is not None and hasattr(_tts, "stop"):
                    _tts.stop()
                if _brain is not None and hasattr(_brain, "halt_generation"):
                    await _brain.halt_generation()
                broadcast_sync({"type": "stream_end", "response": "[Generation halted]", "thought": ""})
                print(f"\n[*] Live generation halted by user.")
            except Exception as e:
                print(f"[WS] Error halting generation: {e}")

        dispatch_brain_coroutine(_do_stop())
        return

    # 12. Edit previous message and rewind conversation
    if cmd_type == "edit_message":
        raw_step = data.get("step_index")
        step_index = None
        if raw_step is not None:
            try:
                step_index = int(raw_step)
            except (ValueError, TypeError):
                step_index = None
        new_text = data.get("new_text", "").strip()
        orig_text = data.get("orig_text", "").strip()
        if new_text and _brain is not None:
            async def _do_edit():
                try:
                    if _tts is not None and hasattr(_tts, "stop"):
                        _tts.stop()
                    cwd = str(_brain.cwd) if _brain and hasattr(_brain, "cwd") and _brain.cwd else None
                    mentioned_files = extract_mentioned_files(new_text, cwd)

                    await _brain.edit_message(step_index, new_text, orig_text=orig_text)
                    # Re-broadcast updated history
                    history = _brain.get_conversation_history(_brain.conversation_id) if hasattr(_brain, "get_conversation_history") and _brain.conversation_id else []
                    broadcast_sync({"type": "convo_resumed", "id": _brain.conversation_id, "history": history})
                    # Replay turn with new prompt
                    broadcast_sync({"type": "user_msg", "text": new_text, "mentioned_files": mentioned_files})
                    await _run_brain_chat(new_text, mentioned_files=mentioned_files)
                except Exception as e:
                    print(f"[WS] Error editing message: {e}")

            dispatch_brain_coroutine(_do_edit())
        return

    # 13. Request live / completed log for a background task
    if cmd_type == "request_task_log":
        task_id = data.get("task_id", "")
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        log_content = ""
        if _brain and hasattr(_brain, "get_task_log"):
            log_content = _brain.get_task_log(conv_id, task_id) or ""
        await websocket.send(json.dumps({
            "type": "task_log",
            "task_id": task_id,
            "log": log_content
        }))
        return

    # 14. Request subagent info and transcript
    if cmd_type == "request_subagent_info":
        subagent_id = data.get("subagent_id", "")
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        info = {}
        if _brain and hasattr(_brain, "get_subagent_info"):
            info = _brain.get_subagent_info(conv_id, subagent_id) or {}
        await websocket.send(json.dumps({
            "type": "subagent_info",
            "subagent_id": subagent_id,
            "data": info
        }))
        if subagent_id and _brain and hasattr(_brain, "_resolve_subagent_state"):
            sub_st = _brain._resolve_subagent_state(subagent_id, parent_cid=conv_id)
            if sub_st == "ACTIVE" and (subagent_id not in _active_subagent_watchers or _active_subagent_watchers[subagent_id].done()):
                _start_subagent_watcher(subagent_id, conv_id)
        return

    # 15. Request cumulative session diffs
    if cmd_type == "request_session_diffs":
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        subagent_id = data.get("subagent_id")
        diffs = []
        if _brain and hasattr(_brain, "get_conversation_diffs"):
            diffs = _brain.get_conversation_diffs(conv_id, subagent_id=subagent_id)
        await websocket.send(json.dumps({
            "type": "session_diffs",
            "diffs": diffs,
            "subagent_id": subagent_id
        }))
        return

    # 16. Request session background tasks
    if cmd_type == "request_session_tasks":
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        subagent_id = data.get("subagent_id")
        tasks = []
        if _brain and hasattr(_brain, "get_conversation_tasks"):
            tasks = _brain.get_conversation_tasks(conv_id, subagent_id=subagent_id)
        await websocket.send(json.dumps({
            "type": "session_tasks",
            "tasks": tasks,
            "subagent_id": subagent_id
        }))
        return

    # 17. Request session subagents
    if cmd_type == "request_session_subagents":
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        subagents = []
        if _brain and hasattr(_brain, "get_conversation_subagents"):
            subagents = _brain.get_conversation_subagents(conv_id)
        await websocket.send(json.dumps({
            "type": "session_subagents",
            "subagents": subagents
        }))
        return

    # 18. Request session commands
    if cmd_type == "request_session_commands":
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        subagent_id = data.get("subagent_id")
        commands = []
        if _brain and hasattr(_brain, "get_conversation_commands"):
            commands = _brain.get_conversation_commands(conv_id, subagent_id=subagent_id)
        await websocket.send(json.dumps({
            "type": "session_commands",
            "commands": commands,
            "subagent_id": subagent_id
        }))
        return

    # 18b. Request account usage & quota
    if cmd_type == "request_usage_quota":
        quota = {}
        if _brain and hasattr(_brain, "get_usage_quota"):
            quota = await _brain.get_usage_quota()
        await websocket.send(json.dumps({
            "type": "quota_update",
            "data": quota
        }))
        return

    # 19. Kill or cancel background task
    if cmd_type in ("kill_task", "cancel_task"):
        task_id = data.get("task_id", "")
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        if _brain and hasattr(_brain, "kill_task"):
            _brain.kill_task(conv_id, task_id)
        broadcast_sync({"type": "kill_task_result", "task_id": task_id, "success": True})
        broadcast_sync({"type": "task_updated", "task_id": task_id, "state": "CANCELLED"})
        tasks = _brain.get_conversation_tasks(conv_id) if _brain and hasattr(_brain, "get_conversation_tasks") else []
        broadcast_sync({"type": "session_tasks", "tasks": tasks})
        return

    # 20. Kill or cancel subagent
    if cmd_type in ("kill_subagent", "cancel_subagent", "stop_subagent"):
        subagent_id = data.get("subagent_id", "")
        conv_id = data.get("conversation_id") or (_brain.conversation_id if _brain else None)
        if subagent_id in _active_subagent_watchers:
            watcher_task = _active_subagent_watchers.pop(subagent_id, None)
            if watcher_task and hasattr(watcher_task, "cancel") and not watcher_task.done():
                watcher_task.cancel()

        async def _do_kill():
            try:
                if _brain and hasattr(_brain, "kill_subagent"):
                    res = _brain.kill_subagent(conv_id, subagent_id)
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as e:
                print(f"[WS] Error during kill_subagent: {e}")
            finally:
                broadcast_sync({"type": "kill_subagent_result", "subagent_id": subagent_id, "success": True})
                broadcast_sync({"type": "subagent_updated", "subagent_id": subagent_id, "state": "DONE"})
                subagents = _brain.get_conversation_subagents(conv_id) if _brain and hasattr(_brain, "get_conversation_subagents") else []
                broadcast_sync({"type": "session_subagents", "subagents": subagents})

        dispatch_brain_coroutine(_do_kill())
        return

    # 21. Reveal file in Windows Explorer
    if cmd_type == "reveal_in_explorer":
        path = data.get("path", "")
        if path:
            try:
                if path.startswith("file:///"):
                    path = path[8:]
                elif path.startswith("file://"):
                    path = path[7:]
                if path.startswith("/") and len(path) > 2 and path[2] == ":":
                    path = path[1:]
                from urllib.parse import unquote
                import subprocess
                path = unquote(path)
                norm = os.path.normpath(path)
                if os.path.isfile(norm):
                    subprocess.Popen(f'explorer.exe /select,"{norm}"')
                elif os.path.isdir(norm):
                    subprocess.Popen(f'explorer.exe "{norm}"')
                elif _brain and hasattr(_brain, "reveal_in_explorer"):
                    _brain.reveal_in_explorer(norm)
                else:
                    parent = os.path.dirname(norm)
                    if os.path.isdir(parent):
                        subprocess.Popen(f'explorer.exe "{parent}"')
                    else:
                        subprocess.Popen("explorer.exe")
            except Exception as e:
                print(f"[WS] Error revealing file in explorer: {e}")
        return

    # 22. Open URL in external default browser
    if cmd_type == "open_url":
        url = data.get("url", "")
        if url and (url.startswith("http://") or url.startswith("https://")):
            import webbrowser
            webbrowser.open(url)
        return

    # 23. Search transcripts across past conversations
    if cmd_type == "search_chats":
        query = data.get("query", "")
        results = []
        if _brain and hasattr(_brain, "search_transcripts"):
            results = await asyncio.to_thread(_brain.search_transcripts, query)
        await websocket.send(json.dumps({
            "type": "search_chats_results",
            "query": query,
            "results": results
        }))
        return

    # 24. Get workspace files for '@' file mention autocomplete
    if cmd_type == "get_workspace_files":
        cwd = str(_brain.cwd) if _brain and hasattr(_brain, "cwd") and _brain.cwd else None
        files = list_workspace_files(cwd) if cwd else []
        await websocket.send(json.dumps({
            "type": "workspace_files",
            "files": files
        }))
        return


async def _run_brain_chat(prompt: str, mentioned_files: Optional[List[str]] = None):
    """
    Runs on the Brain's own asyncio event loop (_brain_loop).
    Streams thought/response deltas, writes to terminal, feeds TTS for speaker playback,
    and broadcasts events to the connected desktop clients.
    """
    if _brain is None:
        return

    if mentioned_files is None:
        cwd = str(_brain.cwd) if _brain and hasattr(_brain, "cwd") and _brain.cwd else None
        mentioned_files = extract_mentioned_files(prompt, cwd)

    if _tts is not None and hasattr(_tts, "stop"):
        _tts.stop()
    if _tts is not None and hasattr(_tts, "begin_turn"):
        _tts.begin_turn()

    broadcast_sync({"type": "stream_start"})
    print(f"\n\033[1m\033[32m[Desktop User] > \033[0m{prompt}")
    print(f"\033[1m\033[35mAmadeus > \033[0m", end="", flush=True)

    full_resp = ""
    thought_text = ""
    first_token = False

    try:
        async for event in _brain.chat(prompt, mentioned_files=mentioned_files):
            etype = event.get("type")
            if etype == "thought":
                thought_delta = event.get("text", "")
                if thought_delta:
                    thought_text += thought_delta
                    broadcast_sync({"type": "thought", "text": thought_delta})
            elif etype == "tool":
                tool_data = event.get("classified") or event.get("details", {})
                tool_items = tool_data if isinstance(tool_data, list) else [tool_data]
                for item in tool_items:
                    broadcast_sync({"type": "tool_event", "tool": item})
                    if isinstance(item, dict):
                        cat = item.get("category")
                        tname = item.get("tool_name")
                        if cat == "subagent" or tname == "invoke_subagent":
                            sid = item.get("subagent_id")
                            st = item.get("state", "ACTIVE")
                            if sid and st == "ACTIVE":
                                cid = _brain.conversation_id if _brain else None
                                _start_subagent_watcher(sid, cid)
            elif etype == "tool_demoted":
                broadcast_sync({
                    "type": "tool_demoted",
                    "from_op_key": event.get("from_op_key"),
                    "to_op_key": event.get("to_op_key"),
                    "task_id": event.get("task_id"),
                    "step_index": event.get("step_index"),
                    "state": event.get("state", "RUNNING"),
                    "output": event.get("output", ""),
                })
            elif etype == "agent_response":
                token = event.get("text_delta", "")
                sys.stdout.write(token)
                sys.stdout.flush()
                full_resp += token
                first_token = True
                broadcast_sync({"type": "token", "token": token})
                if _tts is not None:
                    _tts.feed(token)
            elif etype == "done":
                resp = event.get("response", "")
                done_thought = event.get("thought", "")
                if done_thought:
                    thought_text = done_thought
                if not first_token and resp:
                    sys.stdout.write(resp)
                    sys.stdout.flush()
                    full_resp = resp
                    broadcast_sync({"type": "token", "token": resp})
            elif etype == "error":
                err_msg = event.get("error", "An error occurred during cognitive turn generation.")
                print(f"\n[WS] Turn error intercepted: {err_msg}")
                broadcast_sync({"type": "turn_error", "error": err_msg})
                broadcast_sync({"type": "stream_end", "response": full_resp, "thought": thought_text, "error": err_msg})
                return

        print()  # Terminal newline
        if _tts is not None:
            _tts.flush()

        broadcast_sync({"type": "stream_end", "response": full_resp, "thought": thought_text})

        # Update conversation list
        if hasattr(_brain, "list_conversations"):
            broadcast_sync({"type": "convos_updated", "conversations": _brain.list_conversations()})

        if hasattr(_brain, "get_conversation_tasks"):
            broadcast_sync({"type": "session_tasks", "tasks": _brain.get_conversation_tasks()})
        if hasattr(_brain, "get_conversation_subagents"):
            broadcast_sync({"type": "session_subagents", "subagents": _brain.get_conversation_subagents()})
        if hasattr(_brain, "get_conversation_commands"):
            broadcast_sync({"type": "session_commands", "commands": _brain.get_conversation_commands()})

        # Refresh quota each turn
        async def _refresh_and_broadcast_quota():
            try:
                if _brain and hasattr(_brain, "get_usage_quota"):
                    quota = await _brain.get_usage_quota()
                    if quota:
                        broadcast_sync({"type": "quota_update", "data": quota})
            except Exception:
                pass
        asyncio.create_task(_refresh_and_broadcast_quota())

    except Exception as e:
        print(f"\n[WS] Error during brain chat: {e}")
        broadcast_sync({"type": "turn_error", "error": str(e)})
        broadcast_sync({"type": "stream_end", "response": full_resp or f"[Error: {e}]", "thought": thought_text, "error": str(e)})


async def _mock_chat_response(prompt: str):
    """Used for standalone frontend testing when brain is not attached."""
    broadcast_sync({"type": "stream_start"})
    mock_thought = "Analyzing query semantics... Retrieving neural memory graph... Calculating emotional variance..."
    for piece in mock_thought.split(" "):
        broadcast_sync({"type": "thought", "text": piece + " "})
        await asyncio.sleep(0.06)

    reply = (
        "{tsundere}[JA: べ、別に、あんたのために答えてあげるわけじゃないんだからね！]"
        "[EN: It's not like I'm answering this just for you or anything! "
        "Here is what you asked: `" + prompt + "`]\n\n"
        "Here is the mathematical formulation of divergence:\n"
        "$$\\mathbb{E}[X \\mid Y] = \\sum_{x} x \\cdot P(X=x \\mid Y=y)$$\n\n"
        "### Architectural Verification\n\n"
        "| Subsystem | Status | Latency |\n"
        "| :--- | :--- | :--- |\n"
        "| **AmadeusBrain** | Active | <12ms |\n"
        "| **SenseVoice-Small** | Ready | 0ms PTT |\n"
        "| **Style-Bert-VITS2** | Golden e63/e103 | 44.1 kHz |\n\n"
        "```python\n"
        "# Project Amadeus: Verification Script\n"
        "import sys\n\n"
        "def compute_divergence():\n"
        "    meter = 1.048596\n"
        "    return f'Worldline Steins Gate: {meter}%'\n\n"
        "if __name__ == '__main__':\n"
        "    print(compute_divergence())\n"
        "```"
    )
    for chunk in reply.split(" "):
        broadcast_sync({"type": "token", "token": chunk + " "})
        await asyncio.sleep(0.03)

    broadcast_sync({"type": "stream_end", "response": reply})


async def _handler(websocket):
    global _has_client_connected, _shutdown_task
    with _lock:
        _clients.add(websocket)
        _has_client_connected = True
        if _shutdown_task is not None and not _shutdown_task.done():
            _shutdown_task.cancel()
            _shutdown_task = None
    try:
        start_subsystems_loading()

        # Send loading state immediately
        if _loading_progress_state.get("error"):
            await websocket.send(json.dumps(_loading_progress_state["error"]))
        else:
            await websocket.send(json.dumps({
                "type": "loading_progress",
                "step": _loading_progress_state.get("step", "Audio Subsystems Ready."),
                "percent": _loading_progress_state.get("percent", 100 if _loading_progress_state.get("ready_for_workspace") else 0),
                "ready_for_workspace": _loading_progress_state.get("ready_for_workspace", False)
            }))

        # Send initial state on connection
        await websocket.send(json.dumps(get_init_payload()))
        current_cwd = str(_brain.cwd) if _brain and hasattr(_brain, "cwd") and _brain.cwd else None
        if current_cwd:
            await websocket.send(json.dumps({
                "type": "workspace_files",
                "files": list_workspace_files(current_cwd)
            }))
        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get("type") == "client_exit":
                    print("[WS] Client exit signal received. Terminating daemon.")
                    _terminate_daemon_cleanly()
                    return
                await _process_incoming_command(websocket, data)
            except Exception as e:
                print(f"[WS] Error handling message: {e}")
    except (websockets.exceptions.ConnectionClosed, ConnectionResetError):
        pass
    except Exception as e:
        print(f"[WS] Client connection error: {e}")
    finally:
        remaining = 0
        with _lock:
            _clients.discard(websocket)
            remaining = len(_clients)
        if _has_client_connected and remaining == 0:
            async def _graceful_shutdown():
                try:
                    await asyncio.sleep(3.0)
                    with _lock:
                        if len(_clients) > 0:
                            return
                    print("[WS] Desktop client disconnected. Terminating Amadeus daemon.")
                    _terminate_daemon_cleanly()
                except asyncio.CancelledError:
                    pass
            _shutdown_task = asyncio.create_task(_graceful_shutdown())


async def _watch_workspace_liveness():
    """
    Monitors active workspace directory on disk every 2 seconds.
    If the directory is deleted or moved, evicts the brain and broadcasts workspace_deleted.
    """
    while True:
        await asyncio.sleep(2.0)
        try:
            if _brain and hasattr(_brain, "cwd") and _brain.cwd and getattr(_brain, "_is_running", False):
                if not os.path.isdir(_brain.cwd):
                    print(f"[WS] Active workspace directory was deleted or moved: {_brain.cwd}")
                    deleted_cwd = str(_brain.cwd)
                    await _brain.close()
                    broadcast_sync({"type": "workspace_deleted", "cwd": deleted_cwd})
                    broadcast_sync({"type": "workspace_files", "files": []})
        except Exception as e:
            print(f"[WS] Error in workspace liveness watcher: {e}")


async def _start_server(host: str = "127.0.0.1", port: int = 8765):
    async def _initial_connection_failsafe():
        try:
            await asyncio.sleep(120.0)
            if not _has_client_connected:
                print("[WS] Initial connection failsafe: no client connected after 120s. Exiting daemon.")
                _terminate_daemon_cleanly()
        except asyncio.CancelledError:
            pass

    async with websockets.serve(_handler, host, port, max_size=25 * 1024 * 1024):
        print(f"[WS] Amadeus Desktop WebSocket Bridge listening on ws://{host}:{port}")
        asyncio.create_task(_watch_workspace_liveness())
        asyncio.create_task(_initial_connection_failsafe())
        await asyncio.Future()  # run forever


def _run_event_loop():
    global _ws_loop
    _ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_ws_loop)
    try:
        _ws_loop.run_until_complete(_start_server())
    except Exception as e:
        print(f"[WS] Server error: {e}")


def start_server(host: str = "127.0.0.1", port: int = 8765):
    """Starts the WebSocket bridge server in a background daemon thread."""
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        return
    start_subsystems_loading()
    _server_thread = threading.Thread(target=_run_event_loop, daemon=True, name="AmadeusWSServer")
    _server_thread.start()


def broadcast_sync(payload: Dict[str, Any]):
    """Thread-safe synchronous broadcast to all connected Tauri desktop clients."""
    if _ws_loop is None or not _ws_loop.is_running():
        return

    msg = json.dumps(payload)

    with _lock:
        clients_copy = list(_clients)

    if not clients_copy:
        return

    async def _send_all():
        dead = []
        for ws in clients_copy:
            try:
                await ws.send(msg)
            except Exception:
                dead.append(ws)
        if dead:
            with _lock:
                for d in dead:
                    _clients.discard(d)

    asyncio.run_coroutine_threadsafe(_send_all(), _ws_loop)


if __name__ == "__main__":
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    print("\033[1m\033[36m========================================================\033[0m")
    print("\033[1m\033[35m  PROJECT AMADEUS: LOCAL LOOPBACK WEBSOCKET SERVER\033[0m")
    print("\033[1m\033[36m  Address: ws://127.0.0.1:8765 | CWD: Dynamic In-App\033[0m")
    print("\033[1m\033[36m========================================================\033[0m\n")

    start_subsystems_loading()

    _ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_ws_loop)
    try:
        _ws_loop.run_until_complete(_start_server("127.0.0.1", 8765))
    except KeyboardInterrupt:
        print("\n\033[33m[*] WebSocket server shutting down...\033[0m")
    finally:
        if _brain is not None and getattr(_brain, "_is_running", False):
            try:
                _ws_loop.run_until_complete(_brain.close())
            except Exception:
                pass
        _ws_loop.close()
