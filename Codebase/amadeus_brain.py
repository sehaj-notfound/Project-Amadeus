"""
Project Amadeus: Desktop Agent - Brain Subsystem (Phase 1)
Asynchronous Headless agy Subprocess Runner & NDJSON Stream Demuxer
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Optional, List, Callable

logger = logging.getLogger("amadeus.brain")


def get_amadeus_data_dir() -> Path:
    """Returns the persistent base directory for Project Amadeus data on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ProjectAmadeus"
    return Path.home() / "AppData" / "Local" / "ProjectAmadeus"


def get_amadeus_profile_dir() -> Path:
    """Returns the isolated home/profile directory for Project Amadeus."""
    return get_amadeus_data_dir() / "profile"


def get_host_gemini_dir() -> Path:
    """Returns the user's host ~/.gemini directory."""
    return Path.home() / ".gemini"


def ensure_amadeus_profile(profile_dir: Optional[Path] = None) -> Path:
    """
    Initializes and maintains the isolated Amadeus profile directory.
    Synchronizes authentication and configuration from the host environment
    without leaking conversation history, transcripts, or session metadata.
    """
    if profile_dir is None:
        profile_dir = get_amadeus_profile_dir()

    profile_dir = Path(profile_dir)
    profile_gemini = profile_dir / ".gemini"
    profile_cli = profile_gemini / "antigravity-cli"

    profile_gemini.mkdir(parents=True, exist_ok=True)
    profile_cli.mkdir(parents=True, exist_ok=True)

    host_gemini = get_host_gemini_dir()
    host_cli = host_gemini / "antigravity-cli"

    # Synchronize essential credentials and config from host if available
    sync_root_files = [
        "google_accounts.json",
        "oauth_creds.json",
        "settings.json",
        "trustedFolders.json",
        "installation_id",
        "projects.json",
    ]

    for fname in sync_root_files:
        src = host_gemini / fname
        dst = profile_gemini / fname
        if src.is_file():
            if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    pass

    # Synchronize CLI settings (trusted workspaces, aliases)
    src_cli_settings = host_cli / "settings.json"
    dst_cli_settings = profile_cli / "settings.json"
    if src_cli_settings.is_file():
        if not dst_cli_settings.exists() or src_cli_settings.stat().st_mtime > dst_cli_settings.stat().st_mtime:
            try:
                shutil.copy2(src_cli_settings, dst_cli_settings)
            except Exception:
                pass

    return profile_dir


def ensure_trusted_workspace(folder_path: str, profile_dir: Optional[str] = None) -> None:
    """Ensures folder_path is registered in Antigravity's trusted workspaces headlessly."""
    if not folder_path or not os.path.isdir(folder_path):
        return
    norm_path = os.path.abspath(folder_path)

    if profile_dir and os.path.isdir(profile_dir):
        roots = [profile_dir]
    else:
        roots = [os.path.expanduser("~")]

    for root in roots:
        # 1. Antigravity CLI (settings.json)
        cli_settings = os.path.join(root, ".gemini", "antigravity-cli", "settings.json")
        if os.path.exists(cli_settings):
            try:
                with open(cli_settings, "r", encoding="utf-8") as f:
                    data = json.load(f)
                trusted = data.setdefault("trustedWorkspaces", [])
                if not any(os.path.normcase(p) == os.path.normcase(norm_path) for p in trusted):
                    trusted.append(norm_path)
                    with open(cli_settings, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
            except Exception:
                pass
        else:
            try:
                os.makedirs(os.path.dirname(cli_settings), exist_ok=True)
                data = {"trustedWorkspaces": [norm_path]}
                with open(cli_settings, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

        # 2. Antigravity IDE (trustedFolders.json)
        ide_trusted = os.path.join(root, ".gemini", "trustedFolders.json")
        if os.path.exists(ide_trusted):
            try:
                with open(ide_trusted, "r", encoding="utf-8") as f:
                    data = json.load(f)
                uri_key = norm_path.replace("\\", "/").lower()
                if uri_key not in data:
                    data[uri_key] = "TRUST_FOLDER"
                    with open(ide_trusted, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
            except Exception:
                pass


def is_folder_trusted(folder_path: str, profile_dir: Optional[str] = None) -> bool:
    """Checks whether folder_path is marked as trusted in Antigravity settings or trustedFolders."""
    if not folder_path:
        return False
    norm_path = os.path.abspath(folder_path)
    roots = [os.path.expanduser("~")]
    if profile_dir and os.path.isdir(profile_dir):
        roots.append(profile_dir)

    for root in roots:
        cli_settings = os.path.join(root, ".gemini", "antigravity-cli", "settings.json")
        if os.path.exists(cli_settings):
            try:
                with open(cli_settings, "r", encoding="utf-8") as f:
                    data = json.load(f)
                trusted = data.get("trustedWorkspaces", [])
                if any(os.path.normcase(p) == os.path.normcase(norm_path) for p in trusted):
                    return True
            except Exception:
                pass

        ide_trusted = os.path.join(root, ".gemini", "trustedFolders.json")
        if os.path.exists(ide_trusted):
            try:
                with open(ide_trusted, "r", encoding="utf-8") as f:
                    data = json.load(f)
                uri_key = norm_path.replace("\\", "/").lower()
                if uri_key in data or norm_path in data:
                    return True
            except Exception:
                pass
    return False


def check_cuda_vram_telemetry() -> Dict[str, Any]:
    # TODO: Implement full PyTorch/CUDA VRAM telemetry check (deferred for later polish)
    return {"status": "ok", "cuda_available": False, "note": "Placeholder"}


def verify_model_files(base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Verifies presence of required local models:
    - SenseVoice STT: models/sensevoice/model.int8.onnx, tokens.txt
    - Japanese SBV2: finalised-japanese-engine/Kurisu_e63_s37500.safetensors, config.json
    - English SBV2: finalised-english-engine/Kurisu_EN_e103_s21000.safetensors, config.json (skipped if ja_only)
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent

    # Check hardware profile for ja_only (CPU users run Japanese engine only)
    ja_only = False
    try:
        from hardware_detector import get_or_create_hardware_config
        hw_cfg = get_or_create_hardware_config()
        ja_only = hw_cfg.get("ja_only", False)
    except Exception:
        ja_only = False

    # 1. SenseVoice STT
    sv_dir = base_dir / "models" / "sensevoice"
    sv_model = sv_dir / "model.int8.onnx"
    sv_tokens = sv_dir / "tokens.txt"
    if not sv_model.is_file() or not sv_tokens.is_file():
        raise FileNotFoundError(f"Missing SenseVoice model assets at {sv_dir} (model.int8.onnx, tokens.txt)")

    # 2. Japanese SBV2 (e63)
    jp_dir = base_dir / "finalised-japanese-engine"
    jp_weight = jp_dir / "Kurisu_e63_s37500.safetensors"
    jp_cfg = jp_dir / "config.json"
    if not jp_weight.is_file():
        jp_weight = jp_dir / "model_assets" / "Kurisu_e63_s37500.safetensors"
    if not jp_cfg.is_file():
        jp_cfg = jp_dir / "model_assets" / "config.json"
    if not jp_weight.is_file() or not jp_cfg.is_file():
        raise FileNotFoundError(f"Missing Japanese SBV2 model assets at {jp_dir} (Kurisu_e63_s37500.safetensors, config.json)")

    # 3. English SBV2 (e103) - skipped if ja_only
    en_weight = None
    en_cfg = None
    if not ja_only:
        en_dir = base_dir / "finalised-english-engine"
        en_weight = en_dir / "Kurisu_EN_e103_s21000.safetensors"
        en_cfg = en_dir / "config.json"
        if not en_weight.is_file():
            en_weight = en_dir / "model_assets" / "Kurisu_EN_e103_s21000.safetensors"
        if not en_cfg.is_file():
            en_cfg = en_dir / "model_assets" / "config.json"
        if not en_weight.is_file() or not en_cfg.is_file():
            raise FileNotFoundError(f"Missing English SBV2 model assets at {en_dir} (Kurisu_EN_e103_s21000.safetensors, config.json)")

    return {
        "status": "ok",
        "sensevoice": {"model": str(sv_model), "tokens": str(sv_tokens)},
        "japanese_sbv2": {"weights": str(jp_weight), "config": str(jp_cfg)},
        "english_sbv2": None if ja_only else {"weights": str(en_weight), "config": str(en_cfg)},
    }



_CACHED_MODELS: Optional[List[Dict[str, str]]] = None

def query_agy_models(force_refresh: bool = False) -> List[Dict[str, str]]:
    """
    Dynamically queries installed cognitive models from `agy models`.
    Caches result in memory for fast retrieval.
    """
    global _CACHED_MODELS
    if _CACHED_MODELS and not force_refresh:
        return _CACHED_MODELS

    try:
        res = subprocess.run(
            ["agy", "models"],
            capture_output=True,
            text=True,
            timeout=5,
            shell=(sys.platform == "win32")
        )
        models = []
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.strip().splitlines():
                line = line.strip()
                if not line or line.startswith("Fetching"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    m_id = parts[0].strip()
                    m_name = parts[1].strip()
                    models.append({"id": m_id, "name": m_name, "desc": m_name})
                elif len(parts) == 1 and parts[0]:
                    m_id = parts[0].strip()
                    models.append({"id": m_id, "name": m_id, "desc": m_id})
        if models:
            _CACHED_MODELS = models
            return models
    except Exception:
        pass

    fallback = [
        {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)", "desc": "Gemini 3.8 Flash (High)"},
        {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)", "desc": "Gemini 3.8 Flash (Medium)"},
        {"id": "gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)", "desc": "Gemini 3.8 Flash (Low)"},
        {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Thinking)", "desc": "Claude Sonnet 4.6 (Thinking)"},
        {"id": "claude-opus-4-6-thinking", "name": "Claude Opus 4.6 (Thinking)", "desc": "Claude Opus 4.6 (Thinking)"},
        {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)", "desc": "GPT-OSS 120B (Medium)"},
    ]
    _CACHED_MODELS = fallback
    return fallback


def _resolve_workspace(cwd: Optional[str] = None) -> str:
    """
    Intelligently resolves and ensures a valid workspace directory.
    Prevents WinError 267 (NotADirectoryError) regardless of script placement.
    """
    if cwd:
        target = os.path.abspath(cwd)
        os.makedirs(target, exist_ok=True)
        return target

    this_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(this_dir)

    # 1. Check parent workspace (e.g. Codebase/ -> ../workspace)
    cand_parent = os.path.join(parent_dir, "workspace")
    if os.path.isdir(cand_parent):
        return cand_parent

    # 2. Check local workspace (e.g. ./workspace)
    cand_local = os.path.join(this_dir, "workspace")
    if os.path.isdir(cand_local):
        return cand_local

def classify_tool_event(tool_step: Dict[str, Any], tool_call_index: int = 0) -> Any:
    """
    Classifies a tool event into a normalized representation:
    - command: run_command
    - diff: replace_file_content, write_to_file, apply_diff
    - task: schedule, manage_task
    - subagent: invoke_subagent, manage_subagents
    - generic: read_file, view_file, web_search, etc.
    """
    if "tool_call_index" in tool_step and tool_step["tool_call_index"] is not None:
        tool_call_index = tool_step["tool_call_index"]

    tool_calls = tool_step.get("tool_calls")
    if isinstance(tool_calls, list) and len(tool_calls) > 1:
        results = []
        for idx, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                continue
            sub_step = dict(tool_step)
            sub_step["tool_calls"] = None
            tc_name = tc.get("name") or tc.get("tool_name")
            tc_args = tc.get("args") or tc.get("parameters") or {}
            if isinstance(tc_args, str):
                try:
                    tc_args = json.loads(tc_args)
                except Exception:
                    tc_args = {}
            sub_step["tool_name"] = tc_name
            sub_step["args"] = tc_args
            sub_step["parameters"] = tc_args
            sub_step["tool_info"] = {
                "name": tc_name,
                "parameters": tc_args,
                "output": tc.get("output", tool_step.get("tool_info", {}).get("output", ""))
            }
            sub_step["tool_call_index"] = idx
            res = classify_tool_event(sub_step, tool_call_index=idx)
            if isinstance(res, list):
                results.extend(res)
            elif res:
                results.append(res)
        return results

    tool_name = tool_step.get("tool_name") or tool_step.get("tool_info", {}).get("name")
    if not tool_name and isinstance(tool_calls, list) and len(tool_calls) == 1 and isinstance(tool_calls[0], dict):
        tool_name = tool_calls[0].get("name") or tool_calls[0].get("tool_name")
    if not tool_name:
        tool_name = "unknown"

    state = tool_step.get("state", "ACTIVE")
    info = tool_step.get("tool_info", {})
    params = info.get("parameters", {}) if isinstance(info.get("parameters"), dict) else {}
    if not params:
        raw_p = info.get("args") or tool_step.get("args") or tool_step.get("parameters") or {}
        if isinstance(raw_p, str):
            try:
                params = json.loads(raw_p)
            except Exception:
                params = {}
        elif isinstance(raw_p, dict):
            params = raw_p
    if not params and isinstance(tool_calls, list) and len(tool_calls) == 1 and isinstance(tool_calls[0], dict):
        raw_tc_args = tool_calls[0].get("args") or tool_calls[0].get("parameters") or {}
        if isinstance(raw_tc_args, str):
            try:
                params = json.loads(raw_tc_args)
            except Exception:
                params = {}
        elif isinstance(raw_tc_args, dict):
            params = raw_tc_args
    output = info.get("output", "")
    duration = tool_step.get("duration_seconds", 0)
    step_index = tool_step.get("step_index", 0)
    desc = params.get("Description") or params.get("toolSummary") or params.get("toolAction") or ""

    # 1. Command Execution
    if tool_name == "run_command":
        cmd = params.get("CommandLine", "")
        cwd = params.get("Cwd", "")
        # Check if agy demoted the command into a background task
        m_bg = re.search(r"running as a background task with task id:\s*([^\s\n\r]+)", output) if output else None
        if m_bg:
            task_id = m_bg.group(1).strip("\"'")
            t_key = f"task_{task_id}"
            return {
                "category": "task",
                "op_key": t_key,
                "task_key": t_key,
                "tool_name": tool_name,
                "state": "RUNNING",
                "step_index": step_index,
                "tool_call_index": tool_call_index,
                "task_id": task_id,
                "command": cmd,
                "prompt": f"Command: {cmd}",
                "cwd": cwd,
                "output": output,
                "duration": duration,
            }

        exit_code = 0
        if output:
            m_exit = re.search(r"The command exited with code (\d+)\.", output)
            if m_exit:
                exit_code = int(m_exit.group(1))
            elif "exited with code" in output:
                m_exit2 = re.search(r"exited with code (\d+)", output)
                if m_exit2:
                    exit_code = int(m_exit2.group(1))
            state = "ERROR" if exit_code != 0 else "DONE"
        else:
            state = "ACTIVE"
        cmd_key = f"cmd_{step_index}_{tool_call_index}"
        return {
            "category": "command",
            "op_key": cmd_key,
            "command_key": cmd_key,
            "tool_name": tool_name,
            "state": state,
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "command": cmd,
            "cwd": cwd,
            "description": desc or cmd,
            "output": output,
            "exit_code": exit_code,
            "duration": duration,
        }

    # 2. File Diffs & Edits
    elif tool_name in ("replace_file_content", "write_to_file", "apply_diff"):
        target_file = params.get("TargetFile", "")
        desc = params.get("Description") or params.get("toolSummary") or params.get("toolAction") or ""
        diff_text = ""
        lines_added = 0
        lines_removed = 0

        if tool_name == "replace_file_content":
            if output:
                m_diff = re.search(r"\[diff_block_start\](.*?)\[diff_block_end\]", output, re.DOTALL)
                if m_diff:
                    diff_text = m_diff.group(1).strip()
            if not diff_text:
                target_content = params.get("TargetContent", "")
                replacement = params.get("ReplacementContent", "")
                start_line = params.get("StartLine", 1)
                diff_text = f"@@ -{start_line} +{start_line} @@\n" + "\n".join(f"-{l}" for l in target_content.splitlines()) + "\n" + "\n".join(f"+{l}" for l in replacement.splitlines())
        elif tool_name == "write_to_file":
            code = params.get("CodeContent", "")
            lines = code.splitlines()
            diff_text = "\n".join(f"+{l}" for l in lines[:100])
            if len(lines) > 100:
                diff_text += f"\n... (+{len(lines) - 100} more lines)"
        elif tool_name == "apply_diff":
            diff_text = params.get("DiffContent") or output

        for dline in diff_text.splitlines():
            if dline.startswith("+") and not dline.startswith("+++"):
                lines_added += 1
            elif dline.startswith("-") and not dline.startswith("---"):
                lines_removed += 1

        file_basename = Path(target_file).name if target_file else "file"
        diff_key = f"diff_{step_index}_{tool_call_index}_{file_basename}"
        return {
            "category": "diff",
            "op_key": diff_key,
            "diff_key": diff_key,
            "tool_name": tool_name,
            "state": state,
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "file": target_file,
            "description": desc,
            "diff": diff_text,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "duration": duration,
        }

    # 3. Background Tasks & Schedules (schedule)
    elif tool_name == "schedule":
        prompt = params.get("Prompt", "")
        duration_sec = params.get("DurationSeconds", 0)
        cron_expr = params.get("CronExpression", "")
        task_id = params.get("TaskId", "")
        if output and not task_id:
            m = re.search(r"task id:\s*([^\s\n\r]+)", output)
            if m:
                task_id = m.group(1).strip("\"'")
        t_key = f"task_{task_id}" if task_id else f"task_step_{step_index}_{tool_call_index}"
        return {
            "category": "task",
            "op_key": t_key,
            "task_key": t_key,
            "tool_name": tool_name,
            "state": state,
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "task_id": task_id,
            "prompt": prompt or (f"Timer ({duration_sec}s)" if duration_sec else (f"Cron: {cron_expr}" if cron_expr else "Scheduled Task")),
            "duration_seconds": duration_sec,
            "cron_expression": cron_expr,
            "output": output,
            "duration": duration,
        }

    # 4. Task Management Operations (manage_task)
    elif tool_name == "manage_task":
        action = params.get("Action", "manage")
        task_id = params.get("TaskId", "")
        if output and not task_id:
            m = re.search(r"task id:\s*([^\s\n\r]+)", output)
            if m:
                task_id = m.group(1).strip("\"'")

        desc = ""
        if action == "status":
            desc = f"Inspected Task: {task_id}" if task_id else "Inspected Task Status"
        elif action == "kill":
            desc = f"Cancelled Task: {task_id}" if task_id else "Cancelled Task"
        elif action == "list":
            desc = "Listed Background Tasks"
        elif action == "send_input":
            desc = f"Sent Input to Task: {task_id}" if task_id else "Sent Input to Task"
        else:
            desc = f"Manage Task: {action}"

        task_op_key = f"taskop_{step_index}_{tool_call_index}_{action}_{task_id}"
        return {
            "category": "task_op",
            "op_key": task_op_key,
            "tool_name": tool_name,
            "action": action,
            "task_id": task_id,
            "description": desc,
            "state": "DONE" if state != "ERROR" else "ERROR",
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "output": output,
            "duration": duration,
        }

    # 5. Subagent Management Operations (manage_subagents)
    elif tool_name == "manage_subagents":
        action = params.get("Action", "manage")
        conv_ids = params.get("ConversationIds") or []
        if isinstance(conv_ids, str):
            conv_ids = [conv_ids]

        desc = ""
        if action == "kill":
            desc = f"Terminated Subagent(s): {', '.join(conv_ids)}" if conv_ids else "Terminated Subagents"
        elif action == "list":
            desc = "Listed Active Subagents"
        else:
            desc = f"Manage Subagents: {action}"

        sub_op_key = f"subop_{step_index}_{tool_call_index}_{action}"
        return {
            "category": "subagent_op",
            "op_key": sub_op_key,
            "tool_name": tool_name,
            "action": action,
            "conversation_ids": conv_ids,
            "description": desc,
            "state": "DONE" if state != "ERROR" else "ERROR",
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "output": output,
            "duration": duration,
        }

    # 6. Subagent Spawning (invoke_subagent)
    elif tool_name == "invoke_subagent":
        subagents = params.get("Subagents", [])
        if isinstance(subagents, str):
            try:
                subagents = json.loads(subagents)
            except Exception:
                subagents = []
        action = params.get("Action", "")
        conv_ids = []
        if output:
            conv_ids = re.findall(r'"conversationId":\s*"([^"]+)"', output)
            if not conv_ids:
                conv_ids = re.findall(r"conversation://([a-zA-Z0-9_\-]+)", output)

        if subagents and isinstance(subagents, list):
            results = []
            for idx, sub_info in enumerate(subagents):
                sid = conv_ids[idx] if idx < len(conv_ids) else ""
                s_role = sub_info.get("Role", "") or "subagent"
                call_idx = idx if len(subagents) > 1 else tool_call_index
                sub_key = f"subagent_{sid}" if sid else f"subagent_{step_index}_{call_idx}_{s_role}"
                results.append({
                    "category": "subagent",
                    "op_key": sub_key,
                    "subagent_key": sub_key,
                    "tool_name": tool_name,
                    "state": state,
                    "step_index": step_index,
                    "tool_call_index": call_idx,
                    "subagent_id": sid,
                    "type_name": sub_info.get("TypeName", ""),
                    "role": s_role,
                    "prompt": sub_info.get("Prompt", ""),
                    "action": action,
                    "output": output,
                    "duration": duration,
                })
            return results if len(results) > 1 else (results[0] if results else {})

        sid = conv_ids[0] if conv_ids else ""
        s_role = params.get("Role", "") or "subagent"
        sub_key = f"subagent_{sid}" if sid else f"subagent_{step_index}_{tool_call_index}_{s_role}"
        return {
            "category": "subagent",
            "op_key": sub_key,
            "subagent_key": sub_key,
            "tool_name": tool_name,
            "state": state,
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "subagent_id": sid,
            "type_name": params.get("TypeName", ""),
            "role": s_role,
            "prompt": params.get("Prompt", ""),
            "action": action,
            "output": output,
            "duration": duration,
        }

    # 7. Generic Tools (read_file, web_search, view_file, etc.)
    else:
        desc = params.get("Description") or params.get("toolSummary") or params.get("toolAction") or tool_name
        target = params.get("TargetFile") or params.get("AbsolutePath") or params.get("query") or params.get("Url") or ""
        gen_key = f"gen_{step_index}_{tool_call_index}_{tool_name}"
        return {
            "category": "generic",
            "op_key": gen_key,
            "tool_name": tool_name,
            "state": state,
            "step_index": step_index,
            "tool_call_index": tool_call_index,
            "target": target,
            "description": desc,
            "output": output[:300] if output else "",
            "duration": duration,
        }


class AmadeusBrain:
    def __init__(
        self,
        model: str = "gemini-3.8-flash-low",
        cwd: Optional[str] = None,
        profile_dir: Optional[str] = None
    ):
        self.model = model
        self.cwd = _resolve_workspace(cwd)
        self.profile_dir = Path(profile_dir) if profile_dir else get_amadeus_profile_dir()
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.conversation_id: Optional[str] = None
        self._is_running = False
        self._is_titled = False
        self.conversation_models: Dict[str, str] = {}
        self.on_conversation_updated: Optional[Callable[[], None]] = None
        self._cancelled_tasks: Set[str] = set()
        self._killed_subagents: Set[str] = set()
        self._stopped_subagents: Set[str] = set()
        self._live_demoted_tasks: Dict[str, Dict[str, Any]] = {}

    def _resolve_model_name_to_id(self, raw_name: str) -> Optional[str]:
        if not raw_name:
            return None
        raw_clean = raw_name.strip()
        available = query_agy_models()
        for m in available:
            if m.get("id") == raw_clean:
                return m["id"]
            if m.get("name", "").lower() == raw_clean.lower():
                return m["id"]
        low = raw_clean.lower()
        if "flash" in low and "low" in low: return "gemini-3.8-flash-low"
        if "flash" in low and "high" in low: return "gemini-3.8-flash-high"
        if "flash" in low and "med" in low: return "gemini-3.8-flash-medium"
        if "sonnet" in low: return "claude-sonnet-4-6"
        if "opus" in low: return "claude-opus-4-6-thinking"
        if "gpt-oss" in low: return "gpt-oss-120b-medium"
        return raw_clean

    def _detect_conversation_model(self, conv_id: str) -> Optional[str]:
        target_file = (
            self.profile_dir
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / conv_id
            / ".system_generated"
            / "logs"
            / "transcript_full.jsonl"
        )
        if not target_file.is_file():
            return None
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in reversed(lines):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if data.get("type") == "USER_INPUT":
                        content = data.get("content") or ""
                        m = re.search(r"The user changed setting `Model Selection` from .*? to (.*?)\.\s*(?:No need|$)", content)
                        if m:
                            return self._resolve_model_name_to_id(m.group(1))
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _get_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["USERPROFILE"] = str(self.profile_dir)
        env["HOME"] = str(self.profile_dir)
        return env

    async def start(self, resume_conversation_id: Optional[str] = None):
        """Spawns the headless agy CLI process in stream-json mode, optionally resuming a prior conversation."""
        if not shutil.which("agy"):
            raise FileNotFoundError("The 'agy' binary was not found on PATH. Please ensure Antigravity CLI is installed.")

        os.makedirs(self.cwd, exist_ok=True)
        ensure_amadeus_profile(self.profile_dir)
        ensure_trusted_workspace(self.cwd, str(self.profile_dir))

        cmd = [
            "agy",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", self.model,
            "--dangerously-skip-permissions"
        ]
        
        target_conv_id = resume_conversation_id or self.conversation_id
        if target_conv_id:
            cmd.extend(["--conversation", target_conv_id])

        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self._get_env()
        )
        if self.proc is None or self.proc.pid is None:
            raise RuntimeError("Failed to spawn agy process.")

        await asyncio.sleep(0.4)

        if self.proc.returncode is not None:
            err_msg = (await self.proc.stderr.read()).decode("utf-8", errors="replace") if self.proc.stderr else ""
            raise RuntimeError(f"agy process exited prematurely with code {self.proc.returncode}: {err_msg.strip()}")

        if self.proc.stdin is None or self.proc.stdin.is_closing():
            raise RuntimeError("agy stdin pipe is closed or broken.")

        self._is_running = True
        if target_conv_id:
            self.conversation_id = target_conv_id
            self._is_titled = True

    async def switch_model(self, new_model: str) -> bool:
        """Switches the cognitive model mid-conversation, preserving conversation context."""
        if self.model == new_model and self._is_running:
            return False

        self.model = new_model
        conv_id = self.conversation_id
        if conv_id:
            self.conversation_models[conv_id] = new_model
        await self.close()
        await self.start(resume_conversation_id=conv_id)
        return True

    async def switch_conversation(self, target_id: str) -> bool:
        """Switches the active conversation to target_id and restarts the agy runner with its model."""
        if self.conversation_id == target_id and self._is_running:
            return False

        self.conversation_id = target_id
        self._is_titled = True

        # Restore target conversation model
        target_model = self.conversation_models.get(target_id)
        if not target_model:
            target_model = self._detect_conversation_model(target_id)
            if target_model:
                self.conversation_models[target_id] = target_model
        if target_model:
            self.model = target_model

        await self.close()
        await self.start(resume_conversation_id=target_id)
        return True

    async def halt_generation(self) -> bool:
        """Immediately halts active model response generation and restarts agy runner on active conversation."""
        if not self._is_running or not self.proc:
            return False
        try:
            self.proc.kill()
            await self.proc.wait()
        except Exception:
            pass
        self._is_running = False
        if self.conversation_id:
            await self.start(resume_conversation_id=self.conversation_id)
        return True

    async def edit_message(self, step_index: Optional[int], new_content: str, orig_text: Optional[str] = None) -> bool:
        """
        Rewinds conversation history before step_index, truncating subsequent turns in both
        the authoritative SQLite conversation database and JSONL transcripts/chunks,
        then re-arms agy on the active conversation.
        """
        if not self.conversation_id:
            return False
            
        await self.close()
        
        target_dir = (
            self.profile_dir
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / self.conversation_id
            / ".system_generated"
            / "logs"
        )
        full_log = target_dir / "transcript_full.jsonl"
        log_file = full_log if full_log.is_file() else (target_dir / "transcript.jsonl")

        if step_index is not None:
            try:
                step_index = int(step_index)
            except (ValueError, TypeError):
                step_index = None

        # Resolve step_index from orig_text if step_index was not provided or invalid
        if (step_index is None or step_index < 0) and orig_text and log_file.is_file():
            try:
                matched_step = None
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        entry = json.loads(line)
                        if entry.get("type") == "USER_INPUT":
                            c = entry.get("content") or ""
                            m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", c, re.DOTALL)
                            req = m.group(1).strip() if m else c.strip()
                            clean_req = re.sub(r"^\[[^\]]*(?:Acoustic Tone|First message)[^\]]*\]\s*", "", req)
                            if clean_req == orig_text.strip() or orig_text.strip() in clean_req:
                                matched_step = entry.get("step_index")
                if matched_step is not None:
                    step_index = matched_step
            except Exception as e:
                print(f"[Brain] Error resolving step_index from orig_text: {e}")

        if step_index is None or step_index < 0:
            step_index = 0

        # 1. Truncate SQLite conversation database (authoritative engine state)
        conv_db = self.profile_dir / ".gemini" / "antigravity-cli" / "conversations" / f"{self.conversation_id}.db"
        if conv_db.is_file():
            try:
                conn = sqlite3.connect(str(conv_db), timeout=5.0)
                conn.execute("DELETE FROM steps WHERE idx >= ?", (step_index,))
                conn.commit()
                conn.close()
                wal = conv_db.with_name(f"{self.conversation_id}.db-wal")
                shm = conv_db.with_name(f"{self.conversation_id}.db-shm")
                if wal.exists():
                    try: wal.unlink()
                    except Exception: pass
                if shm.exists():
                    try: shm.unlink()
                    except Exception: pass
            except Exception as e:
                print(f"[Brain] Error rewinding SQLite steps: {e}")

        # 2. Truncate JSONL transcripts and all chunk files
        all_log_files = [
            target_dir / "transcript_full.jsonl",
            target_dir / "transcript.jsonl",
        ]
        chunks_dir = target_dir / "chunks"
        if chunks_dir.is_dir():
            for cf in chunks_dir.glob("**/*.jsonl"):
                all_log_files.append(cf)

        for log_path in all_log_files:
            if log_path.is_file():
                kept_lines = []
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        l_str = line.strip()
                        if not l_str:
                            continue
                        try:
                            entry = json.loads(l_str)
                            s_idx = entry.get("step_index", 0)
                            if s_idx < step_index:
                                kept_lines.append(l_str)
                        except Exception:
                            continue
                with open(log_path, "w", encoding="utf-8") as f:
                    if kept_lines:
                        f.write("\n".join(kept_lines) + "\n")
                    else:
                        f.write("")

        # 3. Update step_count in conversation_summaries.db
        summary_db = self.profile_dir / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
        if summary_db.is_file():
            try:
                conn = sqlite3.connect(str(summary_db), timeout=5.0)
                conn.execute(
                    "UPDATE conversation_summaries SET step_count = ? WHERE conversation_id = ?",
                    (step_index, self.conversation_id)
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

        # 4. Prune orphaned subagents spawned at or after step_index
        subagents_dir = (
            self.profile_dir
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / self.conversation_id
            / ".system_generated"
            / "subagents"
        )
        if subagents_dir.is_dir():
            for sf in list(subagents_dir.glob("*.json")):
                try:
                    s_data = json.loads(sf.read_text(encoding="utf-8"))
                    spawn_idx = s_data.get("spawnStepIndex")
                    sid = s_data.get("conversationId") or sf.stem
                    if spawn_idx is not None and spawn_idx >= step_index:
                        try:
                            sf.unlink()
                        except Exception:
                            pass
                        if sid:
                            sub_brain = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / sid
                            if sub_brain.is_dir():
                                shutil.rmtree(sub_brain, ignore_errors=True)
                            sub_db = self.profile_dir / ".gemini" / "antigravity-cli" / "conversations" / f"{sid}.db"
                            if sub_db.is_file():
                                try:
                                    sub_db.unlink()
                                except Exception:
                                    pass
                                for ext in ["-wal", "-shm"]:
                                    w = sub_db.with_name(f"{sid}.db{ext}")
                                    if w.is_file():
                                        try:
                                            w.unlink()
                                        except Exception:
                                            pass
                except Exception as e:
                    print(f"[Brain] Error pruning orphaned subagent {sf}: {e}")

        # 5. Prune orphaned step output directories >= step_index
        steps_dir = (
            self.profile_dir
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / self.conversation_id
            / ".system_generated"
            / "steps"
        )
        if steps_dir.is_dir():
            for st_dir in list(steps_dir.iterdir()):
                if st_dir.is_dir() and st_dir.name.isdigit():
                    try:
                        if int(st_dir.name) >= step_index:
                            shutil.rmtree(st_dir, ignore_errors=True)
                    except Exception:
                        pass

        await self.start(resume_conversation_id=self.conversation_id)
        return True

    async def reset_session(self):
        """Silently resets the conversation session, clearing conversation ID and history."""
        self.conversation_id = None
        self._is_titled = False
        await self.close()
        await self.start()

    def list_conversations(self, only_amadeus: bool = True) -> List[Dict[str, Any]]:
        """
        Returns a list of conversation records stored in Amadeus's isolated profile.
        Ordered by last_modified_time descending (most recent first).
        """
        db_path = self.profile_dir / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
        if not db_path.is_file():
            return []

        try:
            conn = sqlite3.connect(str(db_path), timeout=3.0)
            cursor = conn.cursor()
            query = """
                SELECT conversation_id, title, preview, last_modified_time, step_count
                FROM conversation_summaries
                WHERE (parent_conversation_id IS NULL OR parent_conversation_id = '')
                  AND (nesting_depth IS NULL OR nesting_depth = 0)
                ORDER BY last_modified_time DESC
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                conv_id, title, preview, last_mod, step_count = row

                # Filter out uninitialized / empty ghost sessions with 0 steps that have no transcript
                if (step_count or 0) == 0:
                    t_full = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs" / "transcript_full.jsonl"
                    t_compact = t_full.with_name("transcript.jsonl")
                    if not t_full.is_file() and not t_compact.is_file():
                        continue

                mod_display = "Unknown"
                if last_mod:
                    try:
                        dt = datetime.fromisoformat(str(last_mod).replace("Z", "+00:00"))
                        mod_display = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        mod_display = str(last_mod)[:19]

                raw_title = (title or "").strip()
                if raw_title:
                    clean_title = raw_title if raw_title.startswith("[AMADEUS]") else f"[AMADEUS] {raw_title}"
                else:
                    clean_title = "[AMADEUS] Untitled Conversation"

                results.append({
                    "id": conv_id,
                    "title": clean_title,
                    "last_modified": mod_display,
                    "step_count": step_count or 0
                })
            return results
        except Exception:
            return []

    @staticmethod
    def _clean_line_dialogue(line: str) -> str:
        text = line
        # 1. Remove emotion tags like {neutral} or {teasing: 0.6, tsundere: 0.4}
        text = re.sub(
            r"\{\s*(?:neutral|tsundere|teasing|flustered|angry|warm|serious|surprised|distressed|tsundere_neutral|back_turned|curious|thinking|embarrassed)(?::\s*[\d.]+)?(?:\s*,\s*(?:neutral|tsundere|teasing|flustered|angry|warm|serious|surprised|distressed|tsundere_neutral|back_turned|curious|thinking|embarrassed)(?::\s*[\d.]+)?)*\s*\}\s*",
            "",
            text,
            flags=re.IGNORECASE
        )
        # 2. Remove [JA: ...] blocks
        text = re.sub(r"\[JA:[\s\S]*?(?:\]|$)\s*", "", text, flags=re.IGNORECASE)
        # 3. Unwrap [EN: ...] blocks
        text = re.sub(r"\[EN:\s*([\s\S]*?)\](?=\s*(?:\{|\[JA:|\[EN:|$|\n))", r"\1", text, flags=re.IGNORECASE)
        text = re.sub(r"\[EN:\s*([\s\S]*)$", r"\1", text, flags=re.IGNORECASE)
        return text

    @classmethod
    def _extract_clean_visual_text(cls, raw_text: str) -> str:
        if not raw_text:
            return ""
        lines = raw_text.splitlines()
        output_lines = []
        in_code_block = False
        opening_fence_length = 0

        for line in lines:
            fence_match = re.match(r"^[ \t]*(`{3,})", line)
            if not in_code_block:
                if fence_match:
                    in_code_block = True
                    opening_fence_length = len(fence_match.group(1))
                    output_lines.append(line)
                else:
                    output_lines.append(cls._clean_line_dialogue(line))
            else:
                output_lines.append(line)
                if fence_match and len(fence_match.group(1)) == opening_fence_length:
                    in_code_block = False
                    opening_fence_length = 0

        return "\n".join(output_lines).strip()

    def search_transcripts(self, query: str = "") -> List[Dict[str, Any]]:
        """
        Searches past user conversations stored in the isolated profile.
        Uses conversation_summaries.db to filter parent conversations
        and order them by latest activity (last_modified_time DESC).

        Titles have their '[AMADEUS]' prefix stripped.
        If query is empty, returns all parent conversations with the last line(s)
        of Amadeus's most recent message from that chat as the snippet.
        If query is non-empty, strictly scans transcript_full.jsonl (with fallback to transcript.jsonl)
        across all parent conversations for matches in USER_INPUT (<USER_REQUEST> text) and
        PLANNER_RESPONSE (Kurisu's speech, cleaned of emotion/JA blocks).
        All matching messages are preserved (never discarded) and returned sorted by recentness.
        """
        db_path = self.profile_dir / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
        if not db_path.is_file():
            return []

        try:
            conn = sqlite3.connect(str(db_path), timeout=3.0)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT conversation_id, title, preview, last_modified_time, step_count
                FROM conversation_summaries
                WHERE (parent_conversation_id IS NULL OR parent_conversation_id = '')
                  AND (nesting_depth IS NULL OR nesting_depth = 0)
                ORDER BY last_modified_time DESC
            """)
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            return []

        def _clean_title(raw_t: Optional[str]) -> str:
            t = (raw_t or "").strip()
            stripped = re.sub(r'^\s*\[AMADEUS\]\s*', '', t, flags=re.IGNORECASE).strip()
            return stripped or "Untitled Conversation"

        q_clean = (query or "").strip()

        # If query is empty, return default listing displaying the most recent reply from Amadeus
        if not q_clean:
            default_results = []
            for row in rows:
                conv_id, raw_title, preview, last_mod, step_count = row
                if (step_count or 0) == 0:
                    t_full = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs" / "transcript_full.jsonl"
                    t_compact = t_full.with_name("transcript.jsonl")
                    if not t_full.is_file() and not t_compact.is_file():
                        continue

                # Extract the last line(s) of Amadeus's most recent message from this chat
                last_reply_snip = ""
                logs_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs"
                transcript_file = logs_dir / "transcript_full.jsonl"
                if not transcript_file.is_file():
                    transcript_file = logs_dir / "transcript.jsonl"

                if transcript_file.is_file():
                    try:
                        with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
                            lines = f.readlines()
                        for line in reversed(lines):
                            line_str = line.strip()
                            if not line_str:
                                continue
                            try:
                                data = json.loads(line_str)
                                if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                                    clean_content = self._extract_clean_visual_text(data["content"])
                                    non_empty = [l.strip() for l in clean_content.splitlines() if l.strip()]
                                    if non_empty:
                                        last_reply_snip = non_empty[-1]
                                        if len(non_empty) > 1 and len(last_reply_snip) < 60:
                                            last_reply_snip = f"{non_empty[-2]} {last_reply_snip}"
                                        break
                            except Exception:
                                continue
                    except Exception:
                        pass

                clean_preview = last_reply_snip.strip() if last_reply_snip else "Conversation archive"

                default_results.append({
                    "conversation_id": conv_id,
                    "title": _clean_title(raw_title),
                    "role": "",
                    "step_index": 0,
                    "snippet": clean_preview,
                    "created_at": str(last_mod or ""),
                    "last_modified": str(last_mod or "")
                })
            return default_results

        # Search transcripts for matching messages
        q_lower = q_clean.lower()
        matches = []

        def _parse_ts(ts_str: Optional[str]) -> float:
            if not ts_str:
                return 0.0
            try:
                s = str(ts_str).replace("Z", "+00:00")
                return datetime.fromisoformat(s).timestamp()
            except Exception:
                pass
            try:
                return datetime.strptime(str(ts_str)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                return 0.0

        for row in rows:
            conv_id, raw_title, preview, last_mod, step_count = row
            clean_title = _clean_title(raw_title)

            logs_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs"
            transcript_file = logs_dir / "transcript_full.jsonl"
            if not transcript_file.is_file():
                transcript_file = logs_dir / "transcript.jsonl"
            if not transcript_file.is_file():
                continue

            try:
                with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue

            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    step = json.loads(line_str)
                except Exception:
                    continue

                stype = step.get("type")
                step_idx = step.get("step_index", 0)
                created_at = step.get("created_at") or str(last_mod or "")

                if stype == "USER_INPUT":
                    raw_content = step.get("content") or ""
                    m_req = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", raw_content, re.DOTALL)
                    text = m_req.group(1).strip() if m_req else raw_content.strip()
                    text = re.sub(r"^\[[^\]]*(?:Acoustic Tone|First message)[^\]]*\]\s*", "", text)
                    role = "user"
                elif stype == "PLANNER_RESPONSE":
                    raw_content = step.get("content") or ""
                    text = self._extract_clean_visual_text(raw_content).strip()
                    role = "model"
                else:
                    continue

                if not text:
                    continue

                pos = text.lower().find(q_lower)
                if pos != -1:
                    # Extract snippet with surrounding context
                    start_snip = max(0, pos - 80)
                    end_snip = min(len(text), pos + len(q_clean) + 100)
                    raw_snip = text[start_snip:end_snip].strip()
                    snippet = ("..." if start_snip > 0 else "") + raw_snip + ("..." if end_snip < len(text) else "")

                    matches.append({
                        "conversation_id": conv_id,
                        "title": clean_title,
                        "role": role,
                        "step_index": step_idx,
                        "snippet": snippet,
                        "created_at": created_at,
                        "last_modified": str(last_mod or "")
                    })

        # Sort all matching messages strictly in order of recentness (descending timestamp & step_index)
        matches.sort(
            key=lambda x: (_parse_ts(x.get("created_at") or x.get("last_modified")), x.get("step_index", 0)),
            reverse=True
        )

        return matches[:200]

    def rename_conversation(self, conv_id: str, new_title: str) -> bool:
        """
        Renames a conversation in both the SQLite summaries DB and annotation pbtxt.
        Ensures [AMADEUS] tag is maintained on disk for session isolation.
        """
        if not conv_id or not new_title:
            return False

        clean_title = new_title.strip()
        if clean_title.startswith("[AMADEUS]"):
            clean_title = clean_title[len("[AMADEUS]"):].strip()
        if not clean_title:
            clean_title = "Untitled Conversation"

        formatted_title = f"[AMADEUS] {clean_title}"

        # 1. Update SQLite DB
        db_path = self.profile_dir / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), timeout=5.0)
                conn.execute(
                    "UPDATE conversation_summaries SET title = ? WHERE conversation_id = ?",
                    (formatted_title, conv_id)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[Brain] Error updating DB title: {e}")

        # 2. Update annotation pbtxt
        annot_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "annotations"
        pbtxt_path = annot_dir / f"{conv_id}.pbtxt"
        try:
            annot_dir.mkdir(parents=True, exist_ok=True)
            with open(pbtxt_path, "w", encoding="utf-8") as f:
                f.write(f'title:"{formatted_title}"\n')
        except Exception as e:
            print(f"[Brain] Error writing annotation pbtxt: {e}")

        return True

    async def delete_conversation(self, conv_id: str) -> bool:
        """
        Deletes a conversation from SQLite DB, annotations, and transcripts.
        If the active conversation was deleted, resets the active session.
        """
        if not conv_id:
            return False

        # 1. Remove from SQLite DB
        db_path = self.profile_dir / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), timeout=5.0)
                conn.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (conv_id,))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[Brain] Error deleting from DB: {e}")

        # 2. Delete annotation pbtxt
        pbtxt_path = self.profile_dir / ".gemini" / "antigravity-cli" / "annotations" / f"{conv_id}.pbtxt"
        if pbtxt_path.exists():
            try:
                pbtxt_path.unlink()
            except Exception:
                pass

        # 3. Delete brain folder (transcripts/artifacts)
        brain_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / conv_id
        if brain_dir.exists():
            try:
                shutil.rmtree(brain_dir, ignore_errors=True)
            except Exception:
                pass

        # 4. If current conversation was deleted, reset session
        if self.conversation_id == conv_id:
            await self.reset_session()

        return True

    def get_available_models(self) -> List[Dict[str, str]]:
        """Queries installed cognitive models dynamically via `agy models`."""
        return query_agy_models()

    @staticmethod
    def _clean_generic_output(content: str, max_lines: int = 25, max_chars: int = 800) -> str:
        """
        Cleans generic tool output preview without slicing words mid-sentence.
        Preserves complete lines matching live stream display.
        """
        if not content:
            return ""
        content = content.strip()
        lines = content.splitlines()
        if len(content) <= max_chars and len(lines) <= max_lines:
            return content

        if len(lines) > 1:
            selected_lines = []
            curr_len = 0
            for line in lines:
                if len(selected_lines) >= max_lines:
                    selected_lines.append("...")
                    break
                if curr_len + len(line) + 1 > max_chars and selected_lines:
                    selected_lines.append("...")
                    break
                selected_lines.append(line)
                curr_len += len(line) + 1
            return "\n".join(selected_lines).strip()
        else:
            if len(content) > max_chars:
                cut = content[:max_chars]
                last_space = cut.rfind(" ")
                if last_space > max_chars // 2:
                    return cut[:last_space].strip() + " ..."
                return cut.strip() + " ..."
            return content

    def get_conversation_history(self, conv_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves the chronological list of user and model messages for conv_id.
        Each model message includes:
        - role: "model"
        - content: markdown/visual response
        - thought: reasoning text
        - step_index: step number
        - created_at: ISO timestamp
        - operations: chronological list of tools/thoughts executed during this turn
        """
        target_id = conv_id or self.conversation_id
        cid = target_id
        if not target_id:
            return []

        full_log = (
            self.profile_dir
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / target_id
            / ".system_generated"
            / "logs"
            / "transcript_full.jsonl"
        )
        compact_log = full_log.with_name("transcript.jsonl")
        transcript_path = full_log if full_log.is_file() else (compact_log if compact_log.is_file() else None)
        if not transcript_path:
            return []

        try:
            # Pass 1: Timeline Collection & Non-Monotonic Rewind (Method A)
            steps_by_idx: Dict[int, Dict[str, Any]] = {}
            previous_user_step_index: Optional[int] = None

            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    s_idx = entry.get("step_index")
                    if s_idx is None:
                        continue
                    s_idx = int(s_idx)
                    etype = entry.get("type")

                    # Non-Monotonic Rewind on USER_INPUT
                    if etype == "USER_INPUT":
                        if previous_user_step_index is not None and s_idx <= previous_user_step_index:
                            keys_to_del = [k for k in steps_by_idx if k >= s_idx]
                            for k in keys_to_del:
                                del steps_by_idx[k]
                        previous_user_step_index = s_idx

                    steps_by_idx[s_idx] = entry

            # Pass 2: Deterministic Step Processing
            messages = []
            pending_ops = []
            consumed_generic_steps = set()
            sorted_steps = sorted(steps_by_idx.keys())
            pending_media_links = []

            for s_idx in sorted_steps:
                if s_idx in consumed_generic_steps:
                    continue

                step = steps_by_idx[s_idx]
                etype = step.get("type")

                if etype == "USER_INPUT":
                    pending_ops = []
                    content = step.get("content") or ""
                    # Check for latest model selection in settings change
                    m_settings = re.search(r"The user changed setting `Model Selection` from .*? to (.*?)\.\s*(?:No need|$)", content)
                    if m_settings:
                        detected = self._resolve_model_name_to_id(m_settings.group(1))
                        if detected:
                            self.conversation_models[target_id] = detected

                    match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                    raw_text = match.group(1).strip() if match else content.strip()
                    clean_text = re.sub(r"^\[[^\]]*(?:Acoustic Tone|First message)[^\]]*\]\s*", "", raw_text)
                    if clean_text:
                        messages.append({
                            "role": "user",
                            "content": clean_text,
                            "step_index": s_idx,
                            "created_at": step.get("created_at")
                        })

                elif etype == "PLANNER_RESPONSE":
                    thinking = (step.get("thinking") or "").strip()
                    t_calls = step.get("tool_calls") or []
                    content = (step.get("content") or "").strip()

                    if content:
                        if pending_media_links:
                            prefix = "\n".join(pending_media_links)
                            if prefix not in content:
                                content = f"{prefix}\n\n{content}"
                            pending_media_links = []

                        messages.append({
                            "role": "model",
                            "content": content,
                            "thought": thinking,
                            "step_index": s_idx,
                            "created_at": step.get("created_at"),
                            "operations": list(pending_ops)
                        })
                        pending_ops = []

                    if thinking and not content:
                        pending_ops.append({
                            "category": "thinking",
                            "op_key": f"{s_idx}_thinking",
                            "thought": thinking,
                            "step_index": s_idx,
                        })

                    if t_calls:
                        for i, tc in enumerate(t_calls):
                            tname = tc.get("name")
                            targs = tc.get("args") or {}
                            if isinstance(targs, str):
                                try:
                                    targs = json.loads(targs)
                                except Exception:
                                    targs = {}

                            res_step_idx = s_idx + 1 + i
                            res_step = steps_by_idx.get(res_step_idx)
                            if res_step:
                                consumed_generic_steps.add(res_step_idx)
                                res_content = res_step.get("content") or ""
                            else:
                                res_content = ""

                            if res_step and isinstance(res_step.get("media"), list):
                                for m in res_step["media"]:
                                    uri = m.get("uri", "")
                                    mime = m.get("mime_type", "")
                                    if uri:
                                        fname = Path(urllib.parse.unquote(uri.replace("file:///", "").replace("file://", ""))).name or "media"
                                        if mime.startswith("image/"):
                                            pending_media_links.append(f"![{fname}]({uri})")
                                        else:
                                            pending_media_links.append(f"[{fname}]({uri})")

                            if tname == "run_command":
                                cmd = targs.get("CommandLine", "")
                                if not res_step:
                                    cmd_key = f"cmd_{s_idx}_{i}"
                                    pending_ops.append({
                                        "category": "command",
                                        "op_key": cmd_key,
                                        "command_key": cmd_key,
                                        "tool_name": tname,
                                        "state": "FAILED",
                                        "command": cmd,
                                        "output": "Tool execution failed or was dropped.",
                                        "exit_code": 1,
                                        "step_index": res_step_idx,
                                        "tool_call_index": i,
                                    })
                                else:
                                    m_bg = re.search(r"running as a background task with task id:\s*([^\s\n\r]+)", res_content)
                                    if m_bg:
                                        task_id = m_bg.group(1).strip("\"'")
                                        if task_id and cid:
                                            t_clean = task_id.split("/")[-1]
                                            task_id = f"{cid}/{t_clean}"
                                        t_key = f"task_{task_id}"
                                        pending_ops.append({
                                            "category": "task",
                                            "op_key": t_key,
                                            "task_key": t_key,
                                            "tool_name": tname,
                                            "state": "RUNNING",
                                            "command": cmd,
                                            "task_id": task_id,
                                            "prompt": f"Command: {cmd}",
                                            "output": res_content.strip(),
                                            "step_index": res_step_idx,
                                            "tool_call_index": i,
                                        })
                                    else:
                                        m_exit = re.search(r"The command exited with code (\d+)\.", res_content)
                                        exit_code = int(m_exit.group(1)) if m_exit else 0
                                        m_out = re.search(r"Output:\n([\s\S]*)", res_content)
                                        output_text = m_out.group(1).strip() if m_out else res_content.strip()
                                        state = "ERROR" if exit_code != 0 else "DONE"
                                        cmd_key = f"cmd_{s_idx}_{i}"
                                        pending_ops.append({
                                            "category": "command",
                                            "op_key": cmd_key,
                                            "command_key": cmd_key,
                                            "tool_name": tname,
                                            "state": state,
                                            "command": cmd,
                                            "output": output_text,
                                            "exit_code": exit_code,
                                            "step_index": res_step_idx,
                                            "tool_call_index": i,
                                        })

                            elif tname in ("replace_file_content", "write_to_file", "apply_diff"):
                                target = targs.get("TargetFile", "")
                                file_basename = Path(target).name if target else "file"
                                diff_key = f"diff_{s_idx}_{i}_{file_basename}"
                                if not res_step:
                                    pending_ops.append({
                                        "category": "diff",
                                        "op_key": diff_key,
                                        "diff_key": diff_key,
                                        "tool_name": tname,
                                        "state": "FAILED",
                                        "file": target,
                                        "diff": "",
                                        "lines_added": 0,
                                        "lines_removed": 0,
                                        "step_index": res_step_idx,
                                        "tool_call_index": i,
                                    })
                                else:
                                    diff_text = ""
                                    m_diff = re.search(r"\[diff_block_start\](.*?)\[diff_block_end\]", res_content, re.DOTALL)
                                    if m_diff:
                                        diff_text = m_diff.group(1).strip()
                                    elif tname == "write_to_file":
                                        code = targs.get("CodeContent", "")
                                        diff_text = "\n".join(f"+{l}" for l in code.splitlines()[:100])
                                    elif tname == "apply_diff":
                                        diff_text = targs.get("DiffContent") or res_content

                                    added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
                                    removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))

                                    pending_ops.append({
                                        "category": "diff",
                                        "op_key": diff_key,
                                        "diff_key": diff_key,
                                        "tool_name": tname,
                                        "state": "DONE",
                                        "file": target,
                                        "diff": diff_text,
                                        "lines_added": added,
                                        "lines_removed": removed,
                                        "step_index": res_step_idx,
                                        "tool_call_index": i,
                                    })

                            elif tname == "manage_task":
                                task_id = targs.get("TaskId", "")
                                action = targs.get("Action", "manage")
                                m_id = re.search(r"task id:\s*([^\s\n\r]+)", res_content)
                                if m_id and not task_id:
                                    task_id = m_id.group(1).strip("\"'")

                                desc = ""
                                if action == "status":
                                    desc = f"Inspected Task: {task_id}" if task_id else "Inspected Task Status"
                                elif action == "kill":
                                    desc = f"Cancelled Task: {task_id}" if task_id else "Cancelled Task"
                                elif action == "list":
                                    desc = "Listed Background Tasks"
                                elif action == "send_input":
                                    desc = f"Sent Input to Task: {task_id}" if task_id else "Sent Input to Task"
                                else:
                                    desc = f"Manage Task: {action}"

                                if action == "kill" or "status: canceled" in res_content.lower() or "cancelled" in res_content.lower():
                                    t_clean = task_id.split("/")[-1] if task_id else ""
                                    for op in pending_ops:
                                        if op.get("category") == "task" and op.get("task_id") and (op["task_id"] == task_id or (t_clean and op["task_id"].split("/")[-1] == t_clean)):
                                            op["state"] = "CANCELLED"
                                    for prev_m in reversed(messages):
                                        for op in prev_m.get("operations") or []:
                                            if op.get("category") == "task" and op.get("task_id") and (op["task_id"] == task_id or (t_clean and op["task_id"].split("/")[-1] == t_clean)):
                                                op["state"] = "CANCELLED"

                                task_op_key = f"taskop_{s_idx}_{i}_{action}_{task_id}"
                                state = "DONE" if res_step else "FAILED"
                                pending_ops.append({
                                    "category": "task_op",
                                    "op_key": task_op_key,
                                    "tool_name": tname,
                                    "action": action,
                                    "task_id": task_id,
                                    "description": desc,
                                    "state": state,
                                    "output": res_content.strip() if res_step else "Task operation failed.",
                                    "step_index": res_step_idx,
                                    "tool_call_index": i,
                                })

                            elif tname == "schedule":
                                prompt = targs.get("Prompt", "")
                                duration_sec = targs.get("DurationSeconds", 0)
                                cron_expr = targs.get("CronExpression", "")
                                m_id = re.search(r"task id:\s*([^\s\n\r]+)", res_content)
                                task_id = m_id.group(1).strip("\"'") if m_id else ""
                                if task_id and cid:
                                    t_clean = task_id.split("/")[-1]
                                    task_id = f"{cid}/{t_clean}"
                                t_key = f"task_{task_id}" if task_id else f"task_step_{res_step_idx}_{i}"
                                state = ("RUNNING" if "running as a background task" in res_content else "DONE") if res_step else "FAILED"
                                pending_ops.append({
                                    "category": "task",
                                    "op_key": t_key,
                                    "task_key": t_key,
                                    "tool_name": tname,
                                    "state": state,
                                    "task_id": task_id,
                                    "prompt": prompt or (f"Timer ({duration_sec}s)" if duration_sec else (f"Cron: {cron_expr}" if cron_expr else "Scheduled Task")),
                                    "duration_seconds": duration_sec,
                                    "cron_expression": cron_expr,
                                    "output": res_content.strip() if res_step else "Schedule failed.",
                                    "step_index": res_step_idx,
                                    "tool_call_index": i,
                                })

                            elif tname == "manage_subagents":
                                action = targs.get("Action", "manage")
                                target_ids = targs.get("ConversationIds") or []
                                if isinstance(target_ids, str):
                                    target_ids = [target_ids]
                                if action == "kill":
                                    for op in pending_ops:
                                        if op.get("category") == "subagent" and (not target_ids or op.get("subagent_id") in target_ids):
                                            op["state"] = "KILLED"
                                    for prev_m in reversed(messages):
                                        for op in prev_m.get("operations") or []:
                                            if op.get("category") == "subagent" and (not target_ids or op.get("subagent_id") in target_ids):
                                                op["state"] = "KILLED"
                                desc = f"Terminated Subagent(s): {', '.join(target_ids)}" if (action == "kill" and target_ids) else ("Listed Active Subagents" if action == "list" else f"Manage Subagents: {action}")
                                sub_op_key = f"subop_{s_idx}_{i}_{action}"
                                state = "DONE" if res_step else "FAILED"
                                pending_ops.append({
                                    "category": "subagent_op",
                                    "op_key": sub_op_key,
                                    "tool_name": tname,
                                    "action": action,
                                    "conversation_ids": target_ids,
                                    "description": desc,
                                    "state": state,
                                    "output": res_content.strip() if res_step else "Subagent management failed.",
                                    "step_index": res_step_idx,
                                    "tool_call_index": i,
                                })

                            elif tname == "invoke_subagent":
                                subagents = targs.get("Subagents", [])
                                if isinstance(subagents, str):
                                    try:
                                        subagents = json.loads(subagents)
                                    except Exception:
                                        subagents = []
                                conv_ids = re.findall(r'"conversationId":\s*"([^"]+)"', res_content)
                                if not conv_ids:
                                    conv_ids = re.findall(r"conversation://([a-zA-Z0-9_\-]+)", res_content)

                                if subagents and isinstance(subagents, list):
                                    for sub_idx, sub_info in enumerate(subagents):
                                        sid = conv_ids[sub_idx] if sub_idx < len(conv_ids) else ""
                                        s_role = sub_info.get("Role", "") or "subagent"
                                        call_idx = sub_idx if len(subagents) > 1 else i
                                        sub_key = f"subagent_{sid}" if sid else f"subagent_{s_idx}_{call_idx}_{s_role}"
                                        resolved_st = self._resolve_subagent_state(sid, "ACTIVE", parent_cid=cid) if sid else ("ACTIVE" if res_step else "FAILED")
                                        pending_ops.append({
                                            "category": "subagent",
                                            "op_key": sub_key,
                                            "subagent_key": sub_key,
                                            "tool_name": tname,
                                            "state": resolved_st,
                                            "subagent_id": sid,
                                            "type_name": sub_info.get("TypeName", ""),
                                            "role": s_role,
                                            "prompt": sub_info.get("Prompt", ""),
                                            "output": res_content.strip() if res_step else "Subagent invocation failed.",
                                            "step_index": res_step_idx,
                                            "tool_call_index": call_idx,
                                        })
                                else:
                                    sid = conv_ids[0] if conv_ids else ""
                                    s_role = targs.get("Role", "") or "subagent"
                                    sub_key = f"subagent_{sid}" if sid else f"subagent_{s_idx}_{i}_{s_role}"
                                    resolved_st = self._resolve_subagent_state(sid, "ACTIVE", parent_cid=cid) if sid else ("ACTIVE" if res_step else "FAILED")
                                    pending_ops.append({
                                        "category": "subagent",
                                        "op_key": sub_key,
                                        "subagent_key": sub_key,
                                        "tool_name": tname,
                                        "state": resolved_st,
                                        "subagent_id": sid,
                                        "type_name": targs.get("TypeName", ""),
                                        "role": s_role,
                                        "prompt": targs.get("Prompt", ""),
                                        "output": res_content.strip() if res_step else "Subagent invocation failed.",
                                        "step_index": res_step_idx,
                                        "tool_call_index": i,
                                    })

                            else:
                                target = targs.get("TargetFile") or targs.get("AbsolutePath") or targs.get("query") or targs.get("Url") or ""
                                gen_key = f"gen_{s_idx}_{i}_{tname}"
                                state = "DONE" if res_step else "FAILED"
                                clean_out = self._clean_generic_output(res_content) if res_step else "Tool execution failed or was dropped."
                                pending_ops.append({
                                    "category": "generic",
                                    "op_key": gen_key,
                                    "tool_name": tname,
                                    "target": target,
                                    "state": state,
                                    "description": targs.get("Description") or targs.get("toolSummary") or targs.get("toolAction") or tname,
                                    "output": clean_out,
                                    "step_index": res_step_idx,
                                    "tool_call_index": i,
                                })

                elif etype == "GENERIC":
                    if s_idx not in consumed_generic_steps and step.get("media"):
                        if isinstance(step.get("media"), list):
                            for m in step["media"]:
                                uri = m.get("uri", "")
                                mime = m.get("mime_type", "")
                                if uri:
                                    fname = Path(urllib.parse.unquote(uri.replace("file:///", "").replace("file://", ""))).name or "media"
                                    if mime.startswith("image/"):
                                        pending_media_links.append(f"![{fname}]({uri})")
                                    else:
                                        pending_media_links.append(f"[{fname}]({uri})")

                elif etype == "SYSTEM_MESSAGE":
                    content = step.get("content") or ""
                    # 1. Handle subagent returns (incoming message from subagent)
                    m_sub_msg = re.search(r"\[Message\]\s+timestamp=.*?sender=([a-zA-Z0-9_\-]+)", content)
                    if m_sub_msg:
                        sub_id = m_sub_msg.group(1)
                        resolved_st = self._resolve_subagent_state(sub_id, "ACTIVE", parent_cid=cid)
                        for op in pending_ops:
                            if op.get("category") == "subagent":
                                if op.get("subagent_id") == sub_id or not op.get("subagent_id"):
                                    op["subagent_id"] = sub_id
                                    op["state"] = resolved_st
                        for prev_m in reversed(messages):
                            for op in prev_m.get("operations") or []:
                                if op.get("category") == "subagent":
                                    if op.get("subagent_id") == sub_id or not op.get("subagent_id"):
                                        op["subagent_id"] = sub_id
                                        op["state"] = resolved_st

                    # 2. Handle timer completion notifications & sender match
                    m_timer_sender = re.search(r"sender=([^\s]+)", content)
                    t_sender = m_timer_sender.group(1) if m_timer_sender else ""
                    is_timer_wake = False
                    if t_sender and re.match(r"^(?:[a-zA-Z0-9_\-]+/)?task-\d+$", t_sender):
                        is_timer_wake = True
                    if is_timer_wake or "Timer fired." in content or "Finished waiting" in content:
                        t_sender_clean = t_sender.split("/")[-1] if t_sender else ""
                        for op in pending_ops:
                            if op.get("category") == "task" and op.get("tool_name") == "schedule":
                                if not t_sender or op.get("task_id") == t_sender or (t_sender_clean and op.get("task_id", "").split("/")[-1] == t_sender_clean):
                                    op["state"] = "COMPLETED"
                        for prev_m in reversed(messages):
                            for op in prev_m.get("operations") or []:
                                if op.get("category") == "task" and op.get("tool_name") == "schedule":
                                    if not t_sender or op.get("task_id") == t_sender or (t_sender_clean and op.get("task_id", "").split("/")[-1] == t_sender_clean):
                                        op["state"] = "COMPLETED"

                    # 3. Handle server restart stopping all background tasks and subagents
                    if "sender=system" in content and "[Notice] All your subagents and background tasks have been stopped due to server restart." in content:
                        for op in pending_ops:
                            if op.get("state") in ("RUNNING", "ACTIVE"):
                                op["state"] = "DONE" if op.get("category") == "subagent" else "INTERRUPTED"
                        for prev_m in messages:
                            for op in prev_m.get("operations") or []:
                                if op.get("state") in ("RUNNING", "ACTIVE"):
                                    op["state"] = "DONE" if op.get("category") == "subagent" else "INTERRUPTED"

                    # 4. Handle task completion or cancellation (skip task_op to prevent shadowing)
                    m_task_event = re.search(r'Task id "([^"]+)" (finished|was canceled)(?: with result:\s*([\s\S]*))?', content)
                    if m_task_event:
                        t_id = m_task_event.group(1)
                        outcome = m_task_event.group(2)
                        res_text = (m_task_event.group(3) or "").strip()
                        lines = [l.strip() for l in res_text.splitlines() if l.strip()]
                        first_line = lines[0] if lines else ""
                        m_code = re.search(r"the command exited with code (\d+)", first_line.lower())
                        exit_code = int(m_code.group(1)) if m_code else 0
                        if outcome == "was canceled":
                            target_state = "CANCELLED"
                        elif m_code and exit_code > 0:
                            target_state = "FAILED"
                        else:
                            target_state = "COMPLETED"

                        t_clean = t_id.split("/")[-1]
                        canonical_tid = f"{cid}/{t_clean}" if cid and "/" not in t_id else t_id
                        found_op = None
                        for op in pending_ops:
                            if op.get("category") == "task" and op.get("task_id") and (op["task_id"] == t_id or op["task_id"] == canonical_tid or op["task_id"].split("/")[-1] == t_clean):
                                found_op = op
                                break
                        if not found_op:
                            for prev_m in reversed(messages):
                                for op in prev_m.get("operations") or []:
                                    if op.get("category") == "task" and op.get("task_id") and (op["task_id"] == t_id or op["task_id"] == canonical_tid or op["task_id"].split("/")[-1] == t_clean):
                                        found_op = op
                                        break
                                if found_op:
                                    break

                        if found_op:
                            found_op["state"] = target_state
                            if res_text:
                                found_op["output"] = res_text
                            if "exit_code" in found_op or found_op.get("command"):
                                found_op["exit_code"] = exit_code
                        else:
                            t_key = f"task_{canonical_tid}"
                            pending_ops.append({
                                "category": "task",
                                "op_key": t_key,
                                "task_key": t_key,
                                "state": target_state,
                                "task_id": canonical_tid,
                                "output": res_text,
                                "exit_code": exit_code,
                                "step_index": s_idx
                            })

            if pending_ops:
                if messages and messages[-1].get("role") == "model":
                    messages[-1].setdefault("operations", []).extend(pending_ops)
                else:
                    last_idx = sorted_steps[-1] if sorted_steps else 0
                    messages.append({
                        "role": "model",
                        "content": "",
                        "thought": "",
                        "step_index": last_idx,
                        "created_at": None,
                        "operations": list(pending_ops),
                    })
                pending_ops = []

            if pending_media_links and messages and messages[-1].get("role") == "model":
                prefix = "\n".join(pending_media_links)
                if prefix not in messages[-1]["content"]:
                    messages[-1]["content"] = f"{prefix}\n\n{messages[-1]['content']}".strip()
                pending_media_links = []

            return messages
        except Exception:
            return []


    def get_task_log(self, conv_id: Optional[str], task_id: str) -> Optional[str]:
        """Reads the live or completed log file for task_id, routing by owner conversation ID."""
        if not task_id:
            return None

        if "/" in task_id:
            owner_cid, task_clean = task_id.split("/", 1)
        else:
            owner_cid = conv_id or self.conversation_id
            task_clean = task_id

        if not owner_cid:
            return None

        tasks_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / owner_cid / ".system_generated" / "tasks"
        if tasks_dir.is_dir():
            for ext in ["", ".log"]:
                cand = tasks_dir / f"{task_clean}{ext}"
                if cand.is_file():
                    try:
                        return cand.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

            for f in tasks_dir.glob("*.log"):
                if task_clean in f.name:
                    try:
                        return f.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

        # Fallback to conv_id or self.conversation_id if owner_cid differed and not found
        fallback_cid = conv_id or self.conversation_id
        if fallback_cid and fallback_cid != owner_cid:
            fb_tasks_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / fallback_cid / ".system_generated" / "tasks"
            if fb_tasks_dir.is_dir():
                for ext in ["", ".log"]:
                    cand = fb_tasks_dir / f"{task_clean}{ext}"
                    if cand.is_file():
                        try:
                            return cand.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            pass
        return None

    def get_subagent_info(self, conv_id: Optional[str], subagent_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves subagent descriptor, state, and step transcript."""
        cid = conv_id or self.conversation_id
        if not cid or not subagent_id:
            return None

        meta_file = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / cid / ".system_generated" / "subagents" / f"{subagent_id}.json"
        subagent_data = {}
        if meta_file.is_file():
            try:
                subagent_data = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        sub_history = self.get_conversation_history(subagent_id)
        subagent_data["history"] = sub_history
        raw_st = subagent_data.get("state", "ACTIVE")
        subagent_data["state"] = self._resolve_subagent_state(subagent_id, raw_st, parent_cid=cid)
        return subagent_data

    def get_conversation_diffs(self, conv_id: Optional[str] = None, subagent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Aggregates all unique modified files and their unified diffs.
        If subagent_id is "amadeus": returns only Amadeus's direct diffs.
        If subagent_id is specified: returns only that subagent's diffs.
        If subagent_id is None: aggregates Amadeus + all child subagents.
        Also tracks deleted files.
        """
        cid = conv_id or self.conversation_id
        if not cid:
            return []

        diffs_by_file: Dict[str, Dict[str, Any]] = {}
        deleted_files: Set[str] = set()

        def _accumulate_diff(norm_fpath: str, op_copy: Dict[str, Any]):
            if norm_fpath in diffs_by_file:
                existing = diffs_by_file[norm_fpath]
                new_diff = (op_copy.get("diff") or "").strip()
                exist_diff = (existing.get("diff") or "").strip()
                if new_diff:
                    if not exist_diff:
                        existing["diff"] = new_diff
                    elif new_diff not in exist_diff:
                        existing["diff"] = exist_diff + "\n\n" + new_diff
                existing["lines_added"] = (existing.get("lines_added") or 0) + (op_copy.get("lines_added") or 0)
                existing["lines_removed"] = (existing.get("lines_removed") or 0) + (op_copy.get("lines_removed") or 0)
                if op_copy.get("deleted"):
                    existing["deleted"] = True
                    existing["status"] = "DELETED"
                if op_copy.get("step_index") is not None:
                    existing["step_index"] = op_copy["step_index"]
            else:
                diffs_by_file[norm_fpath] = op_copy

        def _check_command_for_deletions(cmd_text: str, author_name: str, step_idx: int):
            m = re.search(r'(?:Remove-Item|rm|del|unlink)\s+(?:-Force\s+)?["\']?([^"\'\n\r]+)["\']?', cmd_text, re.IGNORECASE)
            if m:
                target_raw = m.group(1).strip()
                if not os.path.isabs(target_raw):
                    target_abs = str((Path(self.cwd) / target_raw).resolve())
                else:
                    target_abs = str(Path(target_raw).resolve())
                deleted_files.add(target_abs)
                del_op = {
                    "category": "diff",
                    "tool_name": "delete_file",
                    "file": target_abs,
                    "deleted": True,
                    "status": "DELETED",
                    "diff": f"--- {target_abs}\n+++ /dev/null\n@@ -1 +0,0 @@\n- [File deleted from workspace]",
                    "lines_added": 0,
                    "lines_removed": 1,
                    "author_type": "subagent" if author_name != "Amadeus" else "amadeus",
                    "author_name": author_name,
                    "step_index": step_idx
                }
                _accumulate_diff(target_abs, del_op)

        # 1. Fetch Amadeus's direct history if requested
        if subagent_id is None or subagent_id == "amadeus":
            history = self.get_conversation_history(cid)
            for msg in history:
                for op in msg.get("operations") or []:
                    s_idx = op.get("step_index", 0)
                    if op.get("category") == "command":
                        _check_command_for_deletions(op.get("command", ""), "Amadeus", s_idx)
                    elif op.get("category") == "diff":
                        fpath = op.get("file")
                        if fpath:
                            norm_fpath = str(Path(fpath).resolve())
                            op_copy = dict(op)
                            op_copy["file"] = norm_fpath
                            op_copy["author_type"] = "amadeus"
                            op_copy["author_name"] = "Amadeus"
                            if not os.path.exists(norm_fpath) or norm_fpath in deleted_files:
                                op_copy["deleted"] = True
                                op_copy["status"] = "DELETED"
                            else:
                                op_copy["deleted"] = False
                                op_copy["status"] = "MODIFIED"
                            _accumulate_diff(norm_fpath, op_copy)

        # 2. Fetch subagents' diffs if requested
        if subagent_id != "amadeus":
            subagents = self.get_conversation_subagents(cid)
            for s in subagents:
                sid = s.get("subagent_id")
                if not sid:
                    continue
                if subagent_id is not None and subagent_id != sid:
                    continue
                s_role = s.get("role") or s.get("type_name") or "Subagent"
                s_author = f"Subagent: {s_role}"
                try:
                    s_hist = self.get_conversation_history(sid)
                    for msg in s_hist:
                        for op in msg.get("operations") or []:
                            s_idx = op.get("step_index", 0)
                            if op.get("category") == "command":
                                _check_command_for_deletions(op.get("command", ""), s_author, s_idx)
                            elif op.get("category") == "diff":
                                fpath = op.get("file")
                                if fpath:
                                    norm_fpath = str(Path(fpath).resolve())
                                    op_copy = dict(op)
                                    op_copy["file"] = norm_fpath
                                    op_copy["author_type"] = "subagent"
                                    op_copy["author_name"] = s_author
                                    op_copy["subagent_id"] = sid
                                    if not os.path.exists(norm_fpath) or norm_fpath in deleted_files:
                                        op_copy["deleted"] = True
                                        op_copy["status"] = "DELETED"
                                    else:
                                        op_copy["deleted"] = False
                                        op_copy["status"] = "MODIFIED"
                                    _accumulate_diff(norm_fpath, op_copy)
                except Exception:
                    pass

        return list(diffs_by_file.values())

    def get_conversation_tasks(self, conv_id: Optional[str] = None, subagent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Aggregates all background tasks launched in the conversation.
        Supports filtering by author (Amadeus vs Subagent).
        """
        cid = conv_id or self.conversation_id
        if not cid:
            return []

        tasks_by_id: Dict[str, Dict[str, Any]] = {}

        history = self.get_conversation_history(cid)
        for msg in history:
            for op in msg.get("operations") or []:
                if op.get("category") == "task":
                    raw_tid = op.get("task_id") or f"task-step-{op.get('step_index')}"
                    tclean = raw_tid.split("/")[-1]
                    canonical_tid = f"{cid}/{tclean}"
                    op_copy = dict(op)
                    op_copy["task_id"] = canonical_tid
                    op_copy["author_type"] = "amadeus"
                    op_copy["author_name"] = "Amadeus"
                    tasks_by_id[canonical_tid] = op_copy

        # Check authoritative state for each task
        for canonical_tid, op_copy in tasks_by_id.items():
            t_owner, tclean = canonical_tid.split("/", 1)

            # 1. In-memory cancellation check (strictly scoped to canonical_tid: <owner_cid>/<task_id>)
            if canonical_tid in self._cancelled_tasks:
                op_copy["state"] = "CANCELLED"
                continue

            owner_brain = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / t_owner / ".system_generated"
            owner_steps_dir = owner_brain / "steps"
            owner_tasks_dir = owner_brain / "tasks"

            resolved_state = None

            # 2. Sole authoritative check: steps/<idx>/output.txt (very first non-empty line)
            step_idx = None
            if tclean.startswith("task-"):
                s_part = tclean[5:]
                if s_part.isdigit():
                    step_idx = s_part
            elif str(op_copy.get("step_index", "")).isdigit():
                step_idx = str(op_copy.get("step_index"))

            if step_idx:
                step_out = owner_steps_dir / step_idx / "output.txt"
                if step_out.is_file():
                    try:
                        s_txt = step_out.read_text(encoding="utf-8", errors="replace")
                        lines = [l.strip() for l in s_txt.splitlines() if l.strip()]
                        first_line = lines[0] if lines else ""
                        first_lower = first_line.lower()

                        m_c = re.search(r"the command exited with code (\d+)", first_lower)
                        if m_c:
                            code = int(m_c.group(1))
                            op_copy["exit_code"] = code
                            resolved_state = "COMPLETED" if code == 0 else "FAILED"
                        elif "finished waiting" in first_lower:
                            resolved_state = "COMPLETED"
                        elif "cancel" in first_lower:
                            resolved_state = "CANCELLED"
                        elif "encountered error" in first_lower or "error in tool execution" in first_lower:
                            resolved_state = "FAILED"

                        if not op_copy.get("output") or op_copy.get("output") == "Task running in background...":
                            op_copy["output"] = s_txt.strip()
                    except Exception:
                        pass

            if resolved_state:
                op_copy["state"] = resolved_state

        return list(tasks_by_id.values())

    def _resolve_subagent_state(self, subagent_id: str, state_raw: str = "", parent_cid: Optional[str] = None) -> str:
        """
        Determines the true operational state of a subagent based on Antigravity CLI lifecycle rules:
        - Instant kill check (memory set or SUBAGENT_STATE_KILLED on disk) -> KILLED
        - Past conversations: terminal turn with 0 tool calls -> DONE; pending/generic -> INTERRUPTED
        - Active session:
          - in-flight background tasks or generic/pending tool calls -> ACTIVE
          - send_message to parent with no parent reply -> WAITING
          - parent replied -> ACTIVE
          - terminal turn with 0 tool calls and all tasks completed -> DONE
        """
        if not subagent_id:
            return "ACTIVE"

        if "KILL" in (state_raw or ""):
            return "KILLED"

        cid = parent_cid or self.conversation_id
        canonical_sid = f"{cid}/{subagent_id}" if cid else subagent_id
        if subagent_id in self._stopped_subagents or canonical_sid in self._stopped_subagents:
            return "DONE"

        # 1. Check subagent metadata JSON on disk
        spawn_step = 0
        if cid:
            sub_meta = (
                self.profile_dir
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / cid
                / ".system_generated"
                / "subagents"
                / f"{subagent_id}.json"
            )
            if sub_meta.is_file():
                try:
                    data = json.loads(sub_meta.read_text(encoding="utf-8"))
                    disk_state = data.get("state", "")
                    if "KILL" in disk_state:
                        return "KILLED"
                    spawn_step = data.get("spawnStepIndex", 0)
                except Exception:
                    pass

        # 2. Check if parent transcript recorded native agy server restart notice stopping subagents
        if cid:
            parent_log = (
                self.profile_dir
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / cid
                / ".system_generated"
                / "logs"
                / "transcript.jsonl"
            )
            if parent_log.is_file():
                try:
                    with open(parent_log, "r", encoding="utf-8") as pf:
                        for line in pf:
                            if not line.strip():
                                continue
                            ps = json.loads(line)
                            if ps.get("source") == "SYSTEM" and ps.get("type") == "SYSTEM_MESSAGE":
                                c = ps.get("content") or ""
                                if (
                                    "sender=system" in c
                                    and "[Notice] All your subagents and background tasks have been stopped due to server restart." in c
                                ):
                                    notice_step = ps.get("step_index", 0)
                                    if notice_step > spawn_step:
                                        return "DONE"
                except Exception:
                    pass

        is_active = bool(self._is_running and cid and self.conversation_id == cid)

        subagent_log = (
            self.profile_dir
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / subagent_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        if not subagent_log.is_file():
            return "ACTIVE" if is_active else ("DONE" if (state_raw or "") == "DONE" else "INTERRUPTED")

        try:
            with open(subagent_log, "r", encoding="utf-8") as lf:
                lines = [ln.strip() for ln in lf if ln.strip()]
            if not lines:
                return "ACTIVE" if is_active else ("DONE" if (state_raw or "") == "DONE" else "INTERRUPTED")

            steps = []
            for ln in lines:
                try:
                    steps.append(json.loads(ln))
                except Exception:
                    pass

            if not steps:
                return "ACTIVE" if is_active else ("DONE" if (state_raw or "") == "DONE" else "INTERRUPTED")

            last_step = steps[-1]

            # Fatal Execution Error check
            if last_step.get("status") == "ERROR" or last_step.get("type") == "ERROR":
                return "ERROR"

            # Check for Active Child Background Tasks
            tasks_dir = (
                self.profile_dir
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / subagent_id
                / ".system_generated"
                / "tasks"
            )
            steps_dir = (
                self.profile_dir
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / subagent_id
                / ".system_generated"
                / "steps"
            )

            sub_task_ids = set()
            for s in steps:
                if s.get("type") == "GENERIC":
                    c = s.get("content") or ""
                    if "running as a background task with task id:" in c:
                        m = re.search(r"running as a background task with task id:\s*([^\s\n\r]+)", c)
                        if m:
                            sub_task_ids.add(m.group(1))

            has_running_tasks = False
            for tid in sub_task_ids:
                tclean = tid.split("/")[-1] if "/" in tid else tid
                task_finished = False

                # 1. Authoritative check: steps/<idx>/output.txt
                if tclean.startswith("task-"):
                    s_idx_str = tclean[5:]
                    if s_idx_str.isdigit():
                        step_out = steps_dir / s_idx_str / "output.txt"
                        if step_out.is_file():
                            task_finished = True

                # 2. Fallback check: tasks/<tclean>.log
                if not task_finished and tasks_dir.is_dir():
                    cand = tasks_dir / f"{tclean}.log"
                    if cand.is_file():
                        try:
                            txt = cand.read_text(encoding="utf-8", errors="replace")
                            if any(term in txt for term in [
                                "Task cancelled by user",
                                "Status: CANCELED",
                                "The command exited with code",
                                "finished successfully",
                                "Timer fired."
                            ]):
                                task_finished = True
                        except Exception:
                            pass

                if not task_finished:
                    has_running_tasks = True
                    break

            if has_running_tasks:
                return "ACTIVE"

            # Check Explicit Completion Tool
            terminal_tools = {"complete_task", "finish", "return_result"}
            for s in steps:
                for tc in s.get("tool_calls") or []:
                    tname = tc.get("name", "")
                    if tname in terminal_tools:
                        return "DONE"

            # Check Budget Exhaustion
            for s in reversed(steps[-3:]):
                c = s.get("content") or ""
                if "max_turns reached" in c.lower() or "maximum steps exceeded" in c.lower() or "budget exhausted" in c.lower():
                    return "DONE"

            # In Past Conversations (Closed Session)
            if not is_active:
                step_type = last_step.get("type")
                tool_calls = last_step.get("tool_calls") or []
                if step_type == "PLANNER_RESPONSE" and len(tool_calls) == 0:
                    return "DONE"
                if step_type == "GENERIC" or len(tool_calls) > 0:
                    return "INTERRUPTED"
                return "DONE" if (state_raw or "") == "DONE" else "INTERRUPTED"

            # In Active Conversation (Live Streaming Session)
            step_type = last_step.get("type")
            tool_calls = last_step.get("tool_calls") or []
            if step_type == "GENERIC" or len(tool_calls) > 0:
                return "ACTIVE"

            # Dialogue Tracking with Parent
            send_steps = []
            for s_idx, s in enumerate(steps):
                for tc in s.get("tool_calls") or []:
                    if tc.get("name") == "send_message":
                        send_steps.append((s_idx, s, tc.get("args") or {}))

            if send_steps:
                last_send_idx = send_steps[-1][0]
                parent_replied = False
                parent_turn_done = False
                sub_msg_idx = -1

                if cid:
                    parent_log = (
                        self.profile_dir
                        / ".gemini"
                        / "antigravity-cli"
                        / "brain"
                        / cid
                        / ".system_generated"
                        / "logs"
                        / "transcript.jsonl"
                    )
                    if parent_log.is_file():
                        try:
                            p_lines = [json.loads(l) for l in parent_log.read_text(encoding="utf-8").splitlines() if l.strip()]
                            for p_idx, ps in enumerate(p_lines):
                                if ps.get("type") == "SYSTEM_MESSAGE":
                                    c = ps.get("content") or ""
                                    if f"sender={subagent_id}" in c:
                                        sub_msg_idx = p_idx
                            if sub_msg_idx >= 0:
                                for ps in p_lines[sub_msg_idx + 1:]:
                                    for ptc in ps.get("tool_calls") or []:
                                        if ptc.get("name") == "send_message" and (ptc.get("args") or {}).get("Recipient") == subagent_id:
                                            parent_replied = True
                                            break
                                    if ps.get("type") == "PLANNER_RESPONSE" and not (ps.get("tool_calls") or []) and ps.get("status") != "RUNNING":
                                        parent_turn_done = True
                                    elif ps.get("type") in ("USER_INPUT", "SYSTEM_MESSAGE"):
                                        parent_turn_done = True
                        except Exception:
                            pass

                if parent_replied:
                    # If parent replied, check if subagent received the reply and completed handling it
                    sub_received_reply = any(s.get("type") == "SYSTEM_MESSAGE" for s in steps[last_send_idx + 1:])
                    if sub_received_reply and step_type == "PLANNER_RESPONSE" and not tool_calls:
                        return "DONE"
                    return "ACTIVE"

                if sub_msg_idx >= 0 and parent_turn_done:
                    # Parent received subagent's report and finished the turn without replying.
                    # Since subagent finished its terminal turn, it is DONE.
                    if step_type == "PLANNER_RESPONSE" and not tool_calls:
                        return "DONE"

                # Subagent is actively waiting if parent has not yet received or completed handling the message
                return "WAITING"

            # If no send_message: terminal turn with 0 tool calls
            if step_type == "PLANNER_RESPONSE" and not tool_calls:
                return "DONE"

            return "ACTIVE"
        except Exception:
            return "DONE" if "DONE" in (state_raw or "") else (state_raw or "ACTIVE")

    def get_conversation_subagents(self, conv_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists all subagents spawned during the conversation, merging history operations and disk JSONs."""
        cid = conv_id or self.conversation_id
        if not cid:
            return []

        history = self.get_conversation_history(cid)
        subagents_by_id: Dict[str, Dict[str, Any]] = {}
        for msg in history:
            ops = msg.get("operations") or []
            for op in ops:
                if op.get("category") == "subagent":
                    sid = op.get("subagent_id")
                    if sid:
                        subagents_by_id[sid] = dict(op)

        sub_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "brain" / cid / ".system_generated" / "subagents"
        if sub_dir.is_dir():
            for f in sub_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    conv_id_sub = data.get("conversationId", "") or f.stem
                    if not conv_id_sub:
                        continue
                    desc = data.get("subagentDescriptor", {})
                    state_raw = data.get("state", "ACTIVE")
                    clean_state = self._resolve_subagent_state(conv_id_sub, state_raw, cid)

                    if conv_id_sub in subagents_by_id:
                        subagents_by_id[conv_id_sub]["state"] = clean_state
                        if "role" in desc and not subagents_by_id[conv_id_sub].get("role"):
                            subagents_by_id[conv_id_sub]["role"] = desc["role"]
                        if "typeName" in desc and not subagents_by_id[conv_id_sub].get("type_name"):
                            subagents_by_id[conv_id_sub]["type_name"] = desc["typeName"]
                    else:
                        norm = {
                            "category": "subagent",
                            "subagent_id": conv_id_sub,
                            "type_name": desc.get("typeName", ""),
                            "role": desc.get("role", ""),
                            "state": clean_state,
                            "step_index": data.get("spawnStepIndex", 0),
                        }
                        subagents_by_id[conv_id_sub] = norm
                except Exception:
                    pass

        # Resolve state for any subagents that came only from history operations
        for sid, sinfo in subagents_by_id.items():
            current_state = sinfo.get("state", "ACTIVE")
            target_sid = sinfo.get("subagent_id") or sid
            sinfo["state"] = self._resolve_subagent_state(target_sid, current_state, cid)

        return list(subagents_by_id.values())

    def get_conversation_commands(self, conv_id: Optional[str] = None, subagent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Aggregates all commands executed in the conversation.
        If subagent_id is "amadeus": returns only Amadeus's commands.
        If subagent_id is specified: returns only that subagent's commands.
        If subagent_id is None: aggregates Amadeus + all child subagents.
        """
        cid = conv_id or self.conversation_id
        if not cid:
            return []

        commands: List[Dict[str, Any]] = []

        if subagent_id is None or subagent_id == "amadeus":
            history = self.get_conversation_history(cid)
            for msg in history:
                for op in msg.get("operations") or []:
                    if op.get("category") == "command" or op.get("command"):
                        op_copy = dict(op)
                        op_copy["author_type"] = "amadeus"
                        op_copy["author_name"] = "Amadeus"
                        commands.append(op_copy)

        if subagent_id != "amadeus":
            subagents = self.get_conversation_subagents(cid)
            for s in subagents:
                sid = s.get("subagent_id")
                if not sid:
                    continue
                if subagent_id is not None and subagent_id != sid:
                    continue
                s_role = s.get("role") or s.get("type_name") or "Subagent"
                s_author = f"Subagent: {s_role}"
                try:
                    s_hist = self.get_conversation_history(sid)
                    for msg in s_hist:
                        for op in msg.get("operations") or []:
                            if op.get("category") == "command" or op.get("command"):
                                op_copy = dict(op)
                                op_copy["author_type"] = "subagent"
                                op_copy["author_name"] = s_author
                                op_copy["subagent_id"] = sid
                                commands.append(op_copy)
                except Exception:
                    pass

        return commands

    def kill_task(self, conv_id: Optional[str], task_id: str) -> bool:
        """Terminates a running background task process on Windows using taskkill."""
        import psutil

        if not task_id:
            return False

        if "/" in task_id:
            owner_cid, task_clean = task_id.split("/", 1)
        else:
            owner_cid = conv_id or self.conversation_id
            task_clean = task_id

        if not owner_cid:
            return False

        canonical_id = f"{owner_cid}/{task_clean}"
        self._cancelled_tasks.add(canonical_id)

        target_step_idx = None
        if task_clean.startswith("task-"):
            s_num = task_clean[5:]
            if s_num.isdigit():
                target_step_idx = int(s_num)

        candidates = []
        if self.proc and self.proc.pid and psutil.pid_exists(self.proc.pid):
            try:
                candidates = psutil.Process(self.proc.pid).children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                candidates = []

        if not candidates:
            try:
                candidates = list(psutil.process_iter(['pid', 'name']))
            except Exception:
                candidates = []

        for proc in candidates:
            try:
                env = proc.environ()
                if env.get('ANTIGRAVITY_CONVERSATION_ID') != owner_cid:
                    continue

                if target_step_idx is not None:
                    meta = json.loads(env.get('ANTIGRAVITY_SOURCE_METADATA', '{}'))
                    tool_info = meta.get('tool', {}) if isinstance(meta, dict) else {}
                    if not isinstance(tool_info, dict) or tool_info.get('stepIndex') != target_step_idx:
                        continue

                pid = proc.pid
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied, Exception):
                continue

        return True

    cancel_task = kill_task

    async def kill_subagent(self, conv_id: Optional[str] = None, subagent_id: Optional[str] = None) -> bool:
        """
        Stops active subagents in the conversation by atomically tree-killing the runner
        process (and all child worker commands/tasks) and cleanly re-arming agy on the conversation.
        """
        import time
        import psutil
        import subprocess

        target_cid = conv_id or self.conversation_id
        canonical_sid = f"{target_cid}/{subagent_id}" if target_cid and subagent_id else subagent_id

        # 1. Register target subagent and all active subagents for this conversation as stopped
        if subagent_id:
            self._stopped_subagents.add(subagent_id)
            if canonical_sid:
                self._stopped_subagents.add(canonical_sid)

        # Mark all known subagents for this conversation in memory
        if target_cid:
            known_subs = self.get_conversation_subagents(target_cid)
            for s in known_subs:
                sid = s.get("subagent_id")
                if sid:
                    self._stopped_subagents.add(sid)
                    self._stopped_subagents.add(f"{target_cid}/{sid}")

        # 2. If running, execute atomic tree-kill on the agy session runner
        if self.proc and self._is_running:
            session_pid = self.proc.pid
            logger.info(f"[KILL_SUBAGENT] Executing taskkill /F /T on session PID {session_pid} (conv={target_cid})")
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(session_pid)],
                    capture_output=True,
                    check=False
                )
            except Exception as e:
                logger.error(f"[KILL_SUBAGENT] Error executing taskkill: {e}")

            # Verify with psutil that the process tree is dead
            start_wait = time.time()
            while time.time() - start_wait < 1.0:
                if not psutil.pid_exists(session_pid):
                    break
                time.sleep(0.05)

            # 3. Cleanly re-arm agy runner on the conversation
            self._is_running = False
            self.proc = None
            if target_cid:
                logger.info(f"[KILL_SUBAGENT] Re-arming agy session for conversation {target_cid}")
                await self.start(resume_conversation_id=target_cid)

        return True

    cancel_subagent = kill_subagent
    stop_subagent = kill_subagent

    @staticmethod
    def reveal_in_explorer(target_path: str):
        """Reveals file in Windows Explorer, or opens folder if file is deleted."""
        try:
            norm = os.path.normpath(target_path)
            if os.path.isfile(norm):
                subprocess.Popen(f'explorer.exe /select,"{norm}"')
            else:
                folder = norm if os.path.isdir(norm) else os.path.dirname(norm)
                if os.path.exists(folder):
                    subprocess.Popen(f'explorer.exe "{folder}"')
                else:
                    subprocess.Popen(["explorer.exe"])
        except Exception as e:
            print(f"[Brain] Error revealing in explorer: {e}")

    async def _ensure_amadeus_title(self, fallback_title: Optional[str] = None):
        """
        Polls for the native title generated by agy in the background
        and prefixes it with [AMADEUS] in the annotation pbtxt and SQLite database.
        If agy does not generate a title in time, uses fallback_title.
        Guarantees SQLite insertion/update and fires on_conversation_updated callback.
        """
        conv_id = self.conversation_id
        if not conv_id:
            return

        annot_dir = self.profile_dir / ".gemini" / "antigravity-cli" / "annotations"
        pbtxt_path = annot_dir / f"{conv_id}.pbtxt"
        db_path = self.profile_dir / ".gemini" / "antigravity-cli" / "conversation_summaries.db"

        # Wait up to 3.5 seconds for agy's background task to write pbtxt
        model_title = None
        for _ in range(7):
            await asyncio.sleep(0.5)
            if pbtxt_path.exists():
                try:
                    with open(pbtxt_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content.startswith('title:"') and content.endswith('"'):
                        raw = content[7:-1].strip()
                        if raw:
                            model_title = raw
                            break
                except Exception:
                    pass

        # If agy did not produce a title, use fallback_title or "Conversation"
        if not model_title:
            if fallback_title and fallback_title.strip():
                clean_fall = fallback_title.strip().replace("\n", " ")
                model_title = clean_fall[:35] + ("..." if len(clean_fall) > 35 else "")
            else:
                model_title = "Conversation"

        if model_title.startswith("[AMADEUS]"):
            clean_title = model_title[len("[AMADEUS]"):].strip()
        else:
            clean_title = model_title.strip()

        formatted_title = f"[AMADEUS] {clean_title}"

        try:
            annot_dir.mkdir(parents=True, exist_ok=True)
            with open(pbtxt_path, "w", encoding="utf-8") as f:
                f.write(f'title:"{formatted_title}"\n')
        except Exception:
            pass

        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), timeout=5.0)
                cur = conn.cursor()
                cur.execute("SELECT conversation_id FROM conversation_summaries WHERE conversation_id = ?", (conv_id,))
                row = cur.fetchone()
                now_iso = datetime.utcnow().isoformat() + "Z"
                if row:
                    cur.execute(
                        "UPDATE conversation_summaries SET title = ?, last_modified_time = ? WHERE conversation_id = ?",
                        (formatted_title, now_iso, conv_id)
                    )
                else:
                    cur.execute("""
                        INSERT INTO conversation_summaries (conversation_id, title, preview, step_count, last_modified_time, workspace_uris, last_user_input_time)
                        VALUES (?, ?, ?, 1, ?, ?, ?)
                    """, (conv_id, formatted_title, clean_title, now_iso, str(self.cwd), now_iso))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"[Brain] Error updating conversation_summaries: {e}")

        # Trigger callback so WebSocket clients immediately receive updated list
        if self.on_conversation_updated and callable(self.on_conversation_updated):
            try:
                self.on_conversation_updated()
            except Exception:
                pass

    async def chat(self, user_message: str = "", prompt: Optional[str] = None, mentioned_files: Optional[List[str]] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Sends a user message to agy and yields demuxed stream events:
          - {"type": "init", "conversation_id": ...}
          - {"type": "thought", "text": ...}
          - {"type": "tool", "details": ...}
          - {"type": "agent_response", "text_delta": ...}
          - {"type": "done", "response": ..., "thought": ...}
        """
        actual_message = prompt if prompt is not None else user_message
        if not self.proc or not self._is_running or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("AmadeusBrain is not running. Call start() first.")

        # Structure the user input event per agy CLI requirements
        payload = {
            "event": "user",
            "message": {
                "content": actual_message
            }
        }
        if mentioned_files:
            payload["message"]["mentioned_files"] = mentioned_files

        encoded = (json.dumps(payload) + "\n").encode("utf-8")
        self.proc.stdin.write(encoded)
        await self.proc.stdin.drain()

        # Stream NDJSON responses until the turn's result event
        pending_active_tools: List[Dict[str, Any]] = []
        last_active_tool = None
        self._live_demoted_tasks = {}
        pending_live_media_links: List[str] = []

        while self._is_running:
            line = await self.proc.stdout.readline()
            if not line:
                if self.proc:
                    await self.proc.wait()
                    if self.proc.returncode != 0:
                        err_out = (await self.proc.stderr.read()).decode("utf-8", errors="replace") if self.proc.stderr else ""
                        yield {"type": "error", "error": f"agy exited with code {self.proc.returncode}: {err_out.strip()}", "status": "ERROR"}
                break

            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            event_type = data.get("event")

            if event_type == "init":
                self.conversation_id = data.get("conversation_id")
                yield {"type": "init", "conversation_id": self.conversation_id}

            elif event_type == "step_update":
                step = data.get("step_update", {})
                stype = step.get("step_type")

                # In stype == "generic" or whenever step.get("media")
                media_list = step.get("media") or []
                if isinstance(media_list, list) and media_list:
                    for m in media_list:
                        uri = m.get("uri", "")
                        mime = m.get("mime_type", "")
                        if uri:
                            fname = Path(urllib.parse.unquote(uri.replace("file:///", "").replace("file://", ""))).name or "media"
                            if mime.startswith("image/"):
                                pending_live_media_links.append(f"![{fname}]({uri})")
                            else:
                                pending_live_media_links.append(f"[{fname}]({uri})")
                
                if stype == "thought":
                    thought_text = step.get("text_delta") or step.get("thought") or ""
                    if thought_text:
                        yield {"type": "thought", "text": thought_text}

                elif stype == "tool":
                    classified = classify_tool_event(step)
                    tool_name = step.get("tool_name") or step.get("tool_info", {}).get("name")
                    if not tool_name and isinstance(step.get("tool_calls"), list) and step.get("tool_calls"):
                        tool_name = step.get("tool_calls")[0].get("name") or step.get("tool_calls")[0].get("tool_name")
                    if not tool_name:
                        tool_name = "unknown"

                    info = step.get("tool_info", {})
                    params = info.get("parameters", {}) if isinstance(info.get("parameters"), dict) else {}
                    if not params:
                        raw_p = info.get("args") or step.get("args") or step.get("parameters") or {}
                        if isinstance(raw_p, str):
                            try:
                                params = json.loads(raw_p)
                            except Exception:
                                params = {}
                        elif isinstance(raw_p, dict):
                            params = raw_p
                    if not params and isinstance(step.get("tool_calls"), list) and step.get("tool_calls"):
                        raw_tc_args = step.get("tool_calls")[0].get("args") or step.get("tool_calls")[0].get("parameters") or {}
                        if isinstance(raw_tc_args, str):
                            try:
                                params = json.loads(raw_tc_args)
                            except Exception:
                                params = {}
                        elif isinstance(raw_tc_args, dict):
                            params = raw_tc_args

                    s_idx = step.get("step_index", 0)
                    if isinstance(classified, list):
                        for c_idx, c_item in enumerate(classified):
                            t_entry = {
                                "name": c_item.get("tool_name", tool_name),
                                "step_index": s_idx,
                                "tool_call_index": c_item.get("tool_call_index", c_idx),
                                "params": params,
                                "op_key": c_item.get("op_key"),
                                "classified": c_item,
                            }
                            pending_active_tools.append(t_entry)
                            last_active_tool = t_entry
                    else:
                        op_k = classified.get("op_key") if isinstance(classified, dict) else None
                        t_entry = {
                            "name": tool_name,
                            "step_index": s_idx,
                            "tool_call_index": classified.get("tool_call_index", 0) if isinstance(classified, dict) else 0,
                            "params": params,
                            "op_key": op_k,
                            "classified": classified,
                        }
                        pending_active_tools.append(t_entry)
                        last_active_tool = t_entry

                    yield {"type": "tool", "details": step, "classified": classified}

                elif stype == "agent_response":
                    delta = step.get("text_delta", "")
                    if pending_live_media_links:
                        prefix = "\n".join(pending_live_media_links) + "\n\n"
                        pending_live_media_links = []
                        delta = prefix + delta
                    if delta:
                        yield {"type": "agent_response", "text_delta": delta}

                elif stype == "generic":
                    content = step.get("content") or step.get("text") or step.get("message") or ""

                    # 1. Background Demotion
                    m_demoted = re.search(r"running as a background task with task id:\s*([^\s\n\r]+)", content)
                    if m_demoted:
                        task_id = m_demoted.group(1).strip("\"'")
                        t_clean = task_id.split("/")[-1]
                        canonical_id = f"{self.conversation_id}/{t_clean}" if self.conversation_id and "/" not in task_id else task_id
                        
                        matched_tool = None
                        if pending_active_tools:
                            for i, t in enumerate(pending_active_tools):
                                if t.get("name") == "run_command":
                                    matched_tool = pending_active_tools.pop(i)
                                    break
                        if not matched_tool:
                            matched_tool = last_active_tool

                        s_idx = matched_tool["step_index"] if matched_tool and "step_index" in matched_tool else step.get("step_index", 0)
                        tc_idx = matched_tool.get("tool_call_index", 0) if matched_tool else 0
                        cmd_op_key = matched_tool["op_key"] if matched_tool and "op_key" in matched_tool else f"cmd_{s_idx}_{tc_idx}"
                        to_op_key = f"task_{canonical_id}"

                        demoted_info = {
                            "type": "tool_demoted",
                            "from_op_key": cmd_op_key,
                            "to_op_key": to_op_key,
                            "task_id": canonical_id,
                            "step_index": s_idx,
                            "state": "RUNNING",
                            "output": content.strip()
                        }
                        self._live_demoted_tasks[cmd_op_key] = demoted_info
                        yield demoted_info
                        if matched_tool == last_active_tool:
                            last_active_tool = None

                    # 2. Synchronous Command Completion
                    elif (pending_active_tools and any(t.get("name") == "run_command" for t in pending_active_tools)) or (last_active_tool and last_active_tool.get("name") == "run_command"):
                        matched_tool = None
                        if pending_active_tools:
                            for i, t in enumerate(pending_active_tools):
                                if t.get("name") == "run_command":
                                    matched_tool = pending_active_tools.pop(i)
                                    break
                        if not matched_tool:
                            matched_tool = last_active_tool

                        m_exit = re.search(r"exited with code (\d+)", content, re.IGNORECASE)
                        exit_code = int(m_exit.group(1)) if m_exit else 0
                        state = "DONE" if exit_code == 0 else "ERROR"
                        params = matched_tool.get("params", {}) if matched_tool else {}
                        cmd_text = params.get("CommandLine", "")
                        cwd_text = params.get("Cwd", "")
                        desc = params.get("Description") or params.get("toolSummary") or cmd_text
                        s_idx = matched_tool.get("step_index", 0) if matched_tool else step.get("step_index", 0)
                        tc_idx = matched_tool.get("tool_call_index", 0) if matched_tool else 0
                        op_k = matched_tool.get("op_key") if matched_tool and matched_tool.get("op_key") else f"cmd_{s_idx}_{tc_idx}"
                        completed_tool = {
                            "category": "command",
                            "op_key": op_k,
                            "command_key": op_k,
                            "tool_name": "run_command",
                            "state": state,
                            "step_index": s_idx,
                            "tool_call_index": tc_idx,
                            "command": cmd_text,
                            "cwd": cwd_text,
                            "description": desc,
                            "exit_code": exit_code,
                            "output": content.strip()
                        }
                        yield {
                            "type": "tool",
                            "details": step,
                            "classified": completed_tool
                        }
                        if matched_tool == last_active_tool:
                            last_active_tool = None

                    # 3. Subagent ID Binding
                    elif ("conversationId" in content or "conversation://" in content) and ((pending_active_tools and any(t.get("name") == "invoke_subagent" for t in pending_active_tools)) or (last_active_tool and last_active_tool.get("name") == "invoke_subagent")):
                        matched_tool = None
                        if pending_active_tools:
                            for i, t in enumerate(pending_active_tools):
                                if t.get("name") == "invoke_subagent":
                                    matched_tool = pending_active_tools.pop(i)
                                    break
                        if not matched_tool:
                            matched_tool = last_active_tool

                        conv_ids = re.findall(r'"conversationId":\s*"([^"]+)"', content)
                        if not conv_ids:
                            conv_ids = re.findall(r"conversation://([a-zA-Z0-9_\-]+)", content)
                        if conv_ids:
                            s_idx = matched_tool.get("step_index", 0) if matched_tool else 0
                            params = matched_tool.get("params", {}) if matched_tool else {}
                            subagents = params.get("Subagents", [])
                            if isinstance(subagents, str):
                                try:
                                    subagents = json.loads(subagents)
                                except Exception:
                                    subagents = []

                            if len(conv_ids) > 1 and subagents and isinstance(subagents, list):
                                sub_events = []
                                for idx, sid in enumerate(conv_ids):
                                    sub_info = subagents[idx] if idx < len(subagents) else {}
                                    s_role = sub_info.get("Role", "") or "subagent"
                                    sk = f"subagent_{sid}" if sid else f"subagent_{s_idx}_{idx}_{s_role}"
                                    sub_events.append({
                                        "category": "subagent",
                                        "op_key": sk,
                                        "subagent_key": sk,
                                        "tool_name": "invoke_subagent",
                                        "state": "ACTIVE",
                                        "step_index": s_idx,
                                        "tool_call_index": idx,
                                        "subagent_id": sid,
                                        "type_name": sub_info.get("TypeName", ""),
                                        "role": s_role,
                                        "prompt": sub_info.get("Prompt", ""),
                                        "output": content.strip()
                                    })
                                yield {
                                    "type": "tool",
                                    "details": step,
                                    "classified": sub_events
                                }
                            else:
                                sid = conv_ids[0]
                                s_role = params.get("Role", "") or "subagent"
                                if subagents and isinstance(subagents, list) and len(subagents) > 0:
                                    s_role = subagents[0].get("Role", s_role) or "subagent"
                                    type_name = subagents[0].get("TypeName", params.get("TypeName", ""))
                                    prompt_text = subagents[0].get("Prompt", params.get("Prompt", ""))
                                else:
                                    type_name = params.get("TypeName", "")
                                    prompt_text = params.get("Prompt", "")

                                tc_idx = matched_tool.get("tool_call_index", 0) if matched_tool else 0
                                sub_key = f"subagent_{sid}" if sid else f"subagent_{s_idx}_{tc_idx}_{s_role}"
                                updated_sub = {
                                    "category": "subagent",
                                    "op_key": sub_key,
                                    "subagent_key": sub_key,
                                    "tool_name": "invoke_subagent",
                                    "state": "ACTIVE",
                                    "step_index": s_idx,
                                    "tool_call_index": tc_idx,
                                    "subagent_id": sid,
                                    "type_name": type_name,
                                    "role": s_role,
                                    "prompt": prompt_text,
                                    "output": content.strip()
                                }
                                yield {
                                    "type": "tool",
                                    "details": step,
                                    "classified": updated_sub
                                }
                        if matched_tool == last_active_tool:
                            last_active_tool = None

                    # 4. Task ID Binding
                    elif (re.search(r"task id", content, re.IGNORECASE)) and ((pending_active_tools and any(t.get("name") == "schedule" for t in pending_active_tools)) or (last_active_tool and last_active_tool.get("name") == "schedule")):
                        matched_tool = None
                        if pending_active_tools:
                            for i, t in enumerate(pending_active_tools):
                                if t.get("name") == "schedule":
                                    matched_tool = pending_active_tools.pop(i)
                                    break
                        if not matched_tool:
                            matched_tool = last_active_tool

                        m_tid = re.search(r"task id:\s*([^\s\n\r]+)", content, re.IGNORECASE)
                        if not m_tid:
                            m_tid = re.search(r'task id "([^"]+)"', content, re.IGNORECASE)
                        if m_tid:
                            t_raw = m_tid.group(1).strip("\"'")
                            t_clean = t_raw.split("/")[-1]
                            canonical_id = f"{self.conversation_id}/{t_clean}" if self.conversation_id and "/" not in t_raw else t_raw
                            s_idx = matched_tool.get("step_index", 0) if matched_tool else 0
                            tc_idx = matched_tool.get("tool_call_index", 0) if matched_tool else 0
                            params = matched_tool.get("params", {}) if matched_tool else {}
                            prompt_text = params.get("Prompt", "")
                            duration_sec = params.get("DurationSeconds", 0)
                            cron_expr = params.get("CronExpression", "")
                            t_key = f"task_{canonical_id}"
                            updated_task = {
                                "category": "task",
                                "op_key": t_key,
                                "task_key": t_key,
                                "tool_name": "schedule",
                                "state": "RUNNING" if "running as a background task" in content else "DONE",
                                "step_index": s_idx,
                                "tool_call_index": tc_idx,
                                "task_id": canonical_id,
                                "prompt": prompt_text or (f"Timer ({duration_sec}s)" if duration_sec else (f"Cron: {cron_expr}" if cron_expr else "Scheduled Task")),
                                "duration_seconds": duration_sec,
                                "cron_expression": cron_expr,
                                "output": content.strip()
                            }
                            yield {
                                "type": "tool",
                                "details": step,
                                "classified": updated_task
                            }
                        if matched_tool == last_active_tool:
                            last_active_tool = None

                elif stype == "system_message":
                    content = step.get("content") or step.get("text") or step.get("message") or ""
                    if "[Notice] All your subagents and background tasks have been stopped due to server restart." in content:
                        yield {
                            "type": "tool",
                            "details": step,
                            "classified": {
                                "category": "task",
                                "state": "INTERRUPTED",
                                "step_index": step.get("step_index", 0)
                            }
                        }

                    # Task completion or cancellation (background commands / tasks)
                    m_done = re.search(r'Task id "([^"]+)" (finished|was canceled)(?: with result:\s*([\s\S]*))?', content)
                    if m_done:
                        t_id = m_done.group(1)
                        outcome = m_done.group(2)
                        res = (m_done.group(3) or "").strip()
                        m_code = re.search(r"(?:the command )?exited with code (\d+)", res, re.IGNORECASE)
                        exit_code = int(m_code.group(1)) if m_code else 0
                        if outcome == "was canceled":
                            target_state = "CANCELLED"
                        elif exit_code > 0:
                            target_state = "FAILED"
                        else:
                            target_state = "COMPLETED"

                        t_clean = t_id.split("/")[-1]
                        canonical_id = f"{self.conversation_id}/{t_clean}" if self.conversation_id and "/" not in t_id else t_id
                        yield {
                            "type": "tool",
                            "details": step,
                            "classified": {
                                "category": "task",
                                "op_key": f"task_{canonical_id}",
                                "task_key": f"task_{canonical_id}",
                                "state": target_state,
                                "task_id": canonical_id,
                                "output": res,
                                "exit_code": exit_code,
                                "step_index": step.get("step_index", 0)
                            }
                        }

                    # Timer firings
                    m_timer_sender = re.search(r"sender=([^\s]+)", content)
                    t_sender = m_timer_sender.group(1) if m_timer_sender else ""
                    is_timer_wake = False
                    if t_sender and re.match(r"^(?:[a-zA-Z0-9_\-]+/)?task-\d+$", t_sender):
                        is_timer_wake = True
                    if not m_done and (is_timer_wake or "Timer fired." in content or "Finished waiting" in content):
                        t_id = t_sender
                        if not t_id:
                            m_tb = re.search(r'Task id "([^"]+)"', content)
                            if m_tb:
                                t_id = m_tb.group(1)
                            else:
                                m_bare = re.search(r'(task-\d+)', content)
                                if m_bare:
                                    t_id = m_bare.group(1)
                        t_clean = t_id.split("/")[-1] if t_id else ""
                        canonical_id = f"{self.conversation_id}/{t_clean}" if self.conversation_id and "/" not in t_id and t_id else t_id
                        yield {
                            "type": "tool",
                            "details": step,
                            "classified": {
                                "category": "task",
                                "state": "COMPLETED",
                                "task_id": canonical_id,
                                "output": content.strip(),
                                "step_index": step.get("step_index", 0)
                            }
                        }

                    # Subagent messages
                    m_sub_msg = re.search(r"\[Message\]\s+timestamp=.*?sender=([a-zA-Z0-9_\-]+)", content)
                    if not m_sub_msg:
                        m_sub_msg = re.search(r"sender=([a-zA-Z0-9_\-]+)", content)
                    if m_sub_msg:
                        sub_id = m_sub_msg.group(1)
                        if not sub_id.startswith("task-") and sub_id != "system":
                            resolved_st = self._resolve_subagent_state(sub_id, "ACTIVE", parent_cid=self.conversation_id)
                            yield {
                                "type": "tool",
                                "details": step,
                                "classified": {
                                    "category": "subagent",
                                    "subagent_id": sub_id,
                                    "state": resolved_st,
                                    "step_index": step.get("step_index", 0),
                                    "output": content.strip()
                                }
                            }

            elif event_type in ("result", "done"):
                res = data.get("result", {}) if event_type == "result" else data.get("done", {})
                if not isinstance(res, dict):
                    res = {}

                # Intercept agy errors
                if res.get("status") == "ERROR" or "error" in res or data.get("status") == "ERROR" or "error" in data:
                    error_msg = res.get("error") or data.get("error") or "Unknown error"
                    yield {"type": "error", "error": error_msg, "status": "ERROR"}
                    break

                full_resp = res.get("response", "") if isinstance(res, dict) else (data.get("response") or "")
                if not full_resp and isinstance(data.get("result"), str):
                    full_resp = data.get("result")

                if pending_live_media_links:
                    prefix = "\n".join(pending_live_media_links) + "\n\n"
                    if prefix not in full_resp:
                        full_resp = prefix + full_resp
                    pending_live_media_links = []

                # Read latest thinking scratchpad from transcript_full.jsonl (or transcript.jsonl)
                thought_text = ""
                if self.conversation_id:
                    target_dir = (
                        self.profile_dir
                        / ".gemini"
                        / "antigravity-cli"
                        / "brain"
                        / self.conversation_id
                        / ".system_generated"
                        / "logs"
                    )
                    full_log = target_dir / "transcript_full.jsonl"
                    compact_log = target_dir / "transcript.jsonl"
                    log_file = full_log if full_log.is_file() else (compact_log if compact_log.is_file() else None)
                    if log_file:
                        try:
                            with open(log_file, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                            for l in reversed(lines):
                                l_str = l.strip()
                                if not l_str:
                                    continue
                                try:
                                    step_obj = json.loads(l_str)
                                    if step_obj.get("type") == "PLANNER_RESPONSE":
                                        thought_text = step_obj.get("thinking") or ""
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass

                # Out-of-band automatic session naming
                if not self._is_titled and self.conversation_id:
                    self._is_titled = True
                    asyncio.create_task(self._ensure_amadeus_title(fallback_title=actual_message[:35]))

                yield {
                    "type": "done",
                    "response": full_resp,
                    "thought": thought_text,
                    "status": res.get("status", "SUCCESS") if isinstance(res, dict) else "SUCCESS",
                    "usage": res.get("usage", {}) if isinstance(res, dict) else {}
                }
                break

    _run_chat = chat

    async def close(self):
        """Terminates the underlying agy process and cleanly closes pipe streams."""
        self._is_running = False
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                    await self.proc.stdin.wait_closed()
            except Exception:
                pass

            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    self.proc.kill()
                    await self.proc.wait()
                except Exception:
                    pass
            self.proc = None

    async def get_usage_quota(self) -> dict:
        """Runs one-shot agy --output-format json --print /usage and returns quota groups."""
        import shutil, json, asyncio
        agy_bin = shutil.which("agy") or "agy"
        env = os.environ.copy()
        if self.profile_dir:
            env["USERPROFILE"] = str(self.profile_dir)
            env["HOME"] = str(self.profile_dir)
        try:
            proc = await asyncio.create_subprocess_exec(
                agy_bin, "--output-format", "json", "--print", "/usage",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
            if proc.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                cmd_data = data.get("command", {}).get("data", {})
                if cmd_data:
                    return cmd_data
        except Exception as e:
            logger.warning(f"[UsageQuota] Failed to fetch quota: {e}")
        return {}


