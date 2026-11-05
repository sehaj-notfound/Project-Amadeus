"""
Project Amadeus: Phase 2 Interactive Terminal Shell (Push-to-Talk + Hands-Free Edition)
Three input paths into one unified stream: Hold CAPS LOCK to Speak (Release to Transcribe),
say the wake phrase ("Hey Christina" by default) to go fully hands-free, or type text.
Features in-line editable text input, command history, and dynamic model switching via /model.
Connects AmadeusBrain, Style-Bert-VITS2 Dual Voice TTS (44.1 kHz), SenseVoice-Small STT, and Core Audio unmute checks.
"""

import sys
import os
import subprocess

# Auto-trampoline into the dedicated Python 3.10 virtual environment if invoked by system Python
_VENV_PYTHON = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv", "Scripts", "python.exe"))
if os.path.isfile(_VENV_PYTHON) and __name__ == "__main__":
    current_py = os.path.abspath(sys.executable).lower()
    if current_py != _VENV_PYTHON.lower():
        sys.exit(subprocess.call([_VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:]))

import asyncio
import re
import shutil
import time  # wake-window timeout only
import unicodedata
import msvcrt
import ctypes
from ctypes import wintypes
from typing import Optional, Tuple
from amadeus_brain import AmadeusBrain, query_agy_models
from tts_engine import AmadeusTTS
from audio_input import (
    SenseVoiceSTT,
    AmadeusPushToTalk,
    ensure_microphone_unmuted,
)

# Ensure UTF-8 output encoding on Windows consoles
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ANSI Terminal Colors
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GRAY = "\033[90m"

AVAILABLE_MODELS = query_agy_models()

def get_banner(model_id: str) -> str:
    model_name = model_id
    for m in AVAILABLE_MODELS:
        if m["id"] == model_id:
            model_name = m["name"]
            break
    return f"""{RED}{BOLD}
   █████╗ ███╗   ███╗ █████╗ ██████╗ ███████╗██╗   ██╗███████╗
  ██╔══██╗████╗ ████║██╔══██╗██╔══██╗██╔════╝██║   ██║██╔════╝
  ███████║██╔████╔██║███████║██║  ██║█████╗  ██║   ██║███████╗
  ██╔══██║██║╚██╔╝██║██╔══██║██║  ██║██╔══╝  ██║   ██║╚════██║
  ██║  ██║██║ ╚═╝ ██║██║  ██║██████╔╝███████╗╚██████╔╝███████║
  ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝ ╚═════╝ ╚══════╝
{CYAN}  Viktor Chondria University - Brain Science Institute Replica
  Target: Makise Kurisu | Engine: {model_name}
  Sensory In: SenseVoice-Small (Local STT) | Push-to-Talk: Hold [CAPS LOCK]
  Voice Out: Style-Bert-VITS2 (Kurisu JP/EN 44.1 kHz CPU) | Code Stripper: Active{RESET}
  {GRAY}Instructions: Hold CAPS LOCK while speaking (Release to Send) | Or type text
  Commands: '/model' to switch brain | '/resume' to switch convo | '/clear' to reset | '/mute' to toggle voice | '/stop' to halt voice | '/exit' to quit{RESET}
"""

VK_RETURN  = 0x0D   # Used only for post-submit debounce guard
VK_CAPITAL = 0x14   # Caps Lock — Push-to-Talk key
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.restype   = wintypes.HWND
user32.GetAncestor.restype           = wintypes.HWND
user32.GetAncestor.argtypes          = [wintypes.HWND, wintypes.UINT]
kernel32.GetConsoleWindow.restype    = wintypes.HWND
GA_ROOTOWNER = 3

def is_terminal_focused() -> bool:
    """True only when the window hosting this console is the foreground window.
    False when minimized or when another app has focus."""
    console_hwnd = kernel32.GetConsoleWindow()
    if not console_hwnd:
        return True  # can't determine, fail open
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    if fg == console_hwnd:  # classic conhost window
        return True
    # Windows Terminal / ConPTY: hidden console window is owned by the real terminal window
    return fg == user32.GetAncestor(console_hwnd, GA_ROOTOWNER)

def is_enter_physically_down() -> bool:
    """Returns True if the physical Enter key is currently pressed down (used for debounce only)."""
    return bool(user32.GetAsyncKeyState(VK_RETURN) & 0x8000)

def is_ptt_pressed() -> bool:
    """Returns True while Caps Lock is physically held down (Push-to-Talk gate)."""
    return bool(user32.GetAsyncKeyState(VK_CAPITAL) & 0x8000)

def _restore_capslock_led(was_on: bool) -> None:
    """Restores Caps Lock LED to its pre-PTT toggle state to prevent accidental toggling."""
    is_on_now = bool(user32.GetKeyState(VK_CAPITAL) & 0x0001)
    if is_on_now != was_on:
        ctypes.windll.user32.keybd_event(VK_CAPITAL, 0x3A, 0, 0)  # key down
        ctypes.windll.user32.keybd_event(VK_CAPITAL, 0x3A, 2, 0)  # key up (KEYEVENTF_KEYUP)



# ── Multi-row input rendering ────────────────────────────────────────────────
# When prompt + typed text is wider than the terminal it wraps onto several rows.
# The old redraw used "\r\033[K", which only clears the row the cursor is on, so every
# keystroke reprinted the whole prompt below a stale copy (the repeated "You [Hold CAPS
# LOCK...]" lines). These helpers track the cursor's row so a redraw can climb back to the
# first row, clear everything below it, and repaint.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _cell_width(ch: str) -> int:
    """Terminal columns a character occupies (CJK/emoji = 2, combining marks = 0)."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _cells(s: str) -> int:
    return sum(_cell_width(c) for c in s)


def term_width() -> int:
    return max(20, shutil.get_terminal_size((120, 30)).columns)


def input_end_row(prompt_str: str, typed_chars: list, width: int) -> int:
    """Row (relative to the prompt's first row) holding the last typed character."""
    return (_cells(_ANSI_RE.sub("", prompt_str)) + _cells("".join(typed_chars))) // width


def render_input(prompt_str: str, typed_chars: list, cursor_pos: int, cur_row: int, width: int):
    """
    Returns (escape_sequence, new_cursor_row) that repaints prompt + text and leaves the
    cursor at cursor_pos. cur_row is the row the cursor is on NOW (0 = the prompt's row).
    """
    prompt_cells = _cells(_ANSI_RE.sub("", prompt_str))
    text = "".join(typed_chars)
    total = prompt_cells + _cells(text)
    cursor_abs = prompt_cells + _cells("".join(typed_chars[:cursor_pos]))

    out = ""
    if cur_row > 0:
        out += f"\033[{cur_row}A"                 # climb back to the prompt's first row
    out += f"\r\033[J{prompt_str}{text}"          # clear it and everything below, repaint
    if total % width == 0:
        out += " \r"                              # text ends exactly at the edge: force the wrap
    end_row = total // width
    row, col = divmod(cursor_abs, width)
    if end_row > row:
        out += f"\033[{end_row - row}A"           # cursor is mid-text: climb to its row
    out += f"\033[{col + 1}G"                     # and column
    return out, row


async def read_input_line(
    prompt_str: str,
    ptt: Optional[AmadeusPushToTalk] = None,
    tts: Optional[AmadeusTTS] = None,
    history: Optional[list] = None,
) -> Tuple[str, Optional[str]]:
    """
    Reads a line of user input with full in-line editing (arrows, home, end, backspace, delete, history),
    and seamless push-to-talk activation when holding CAPS LOCK on an empty buffer.
    Returns: (text, emotion) where emotion is None for typed text.
    """
    if history is None:
        history = []

    # Drain any residual keyboard events
    while msvcrt.kbhit():
        msvcrt.getch()

    sys.stdout.write(prompt_str)
    sys.stdout.flush()

    typed_chars = []
    cursor_pos = 0
    history_idx = len(history)
    draft_input = ""
    cur_row = [0]   # terminal row (0 = the prompt's first row) the cursor is currently on

    def redraw():
        out, cur_row[0] = render_input(prompt_str, typed_chars, cursor_pos, cur_row[0], term_width())
        sys.stdout.write(out)
        sys.stdout.flush()

    while True:
        # PTT: Caps Lock held + terminal focused + empty buffer → start voice recording
        if ptt is not None and is_ptt_pressed() and is_terminal_focused() and not typed_chars:
            # Save Caps Lock LED state so we can restore it after PTT ends
            caps_led_before = bool(user32.GetKeyState(VK_CAPITAL) & 0x0001)

            res = ptt.record_while_pressed(
                is_pressed_fn=is_ptt_pressed,
                on_start_recording=lambda: tts.stop() if tts else None
            )

            # Restore Caps Lock LED to prevent accidental toggle
            _restore_capslock_led(caps_led_before)

            # Drain any buffered key events accumulated during recording
            while msvcrt.kbhit():
                msvcrt.getch()

            text = res.get("clean_text", "").strip()
            user_emotion = res.get("emotion", "NEUTRAL")
            if text:
                print(f"\r\033[K{BOLD}{GREEN}You (Voice: {user_emotion}) > {RESET}{text}")
                return text, user_emotion
            else:
                print(f"\r\033[K{GRAY}[No speech detected]{RESET}")
                sys.stdout.write(prompt_str)
                sys.stdout.flush()
                cur_row[0] = 0
                continue

        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ('\r', '\n'):
                if typed_chars:
                    down = input_end_row(prompt_str, typed_chars, term_width()) - cur_row[0]
                    if down > 0:   # cursor was moved up mid-text; don't overwrite the tail
                        sys.stdout.write(f"\033[{down}B")
                    print()
                    user_input = "".join(typed_chars).strip()
                    while msvcrt.kbhit():
                        msvcrt.getch()
                    while is_enter_physically_down():
                        await asyncio.sleep(0.01)
                    if user_input and (not history or history[-1] != user_input):
                        history.append(user_input)
                    return user_input, None
            elif ch == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif ch in ('\x00', '\xe0'):
                code = msvcrt.getwch()
                if code == 'K':  # Left arrow
                    if cursor_pos > 0:
                        cursor_pos -= 1
                        redraw()
                elif code == 'M':  # Right arrow
                    if cursor_pos < len(typed_chars):
                        cursor_pos += 1
                        redraw()
                elif code == 'G':  # Home
                    cursor_pos = 0
                    redraw()
                elif code == 'O':  # End
                    cursor_pos = len(typed_chars)
                    redraw()
                elif code == 'S':  # Delete
                    if cursor_pos < len(typed_chars):
                        del typed_chars[cursor_pos]
                        redraw()
                elif code == 'H':  # Up arrow (history previous)
                    if history and history_idx > 0:
                        if history_idx == len(history):
                            draft_input = "".join(typed_chars)
                        history_idx -= 1
                        typed_chars = list(history[history_idx])
                        cursor_pos = len(typed_chars)
                        redraw()
                elif code == 'P':  # Down arrow (history next)
                    if history and history_idx < len(history):
                        history_idx += 1
                        if history_idx == len(history):
                            typed_chars = list(draft_input)
                        else:
                            typed_chars = list(history[history_idx])
                        cursor_pos = len(typed_chars)
                        redraw()
            elif ch == '\x08':  # Backspace
                if cursor_pos > 0:
                    del typed_chars[cursor_pos - 1]
                    cursor_pos -= 1
                    redraw()
            elif ord(ch) >= 32:  # Printable character
                typed_chars.insert(cursor_pos, ch)
                cursor_pos += 1
                redraw()

        await asyncio.sleep(0.01)

async def prompt_model_selector(brain: AmadeusBrain, arg: str = "") -> None:
    """Displays an in-terminal selector for cognitive models and switches brain cleanly."""
    models = query_agy_models()
    arg = arg.strip().lower()
    chosen_id = None

    if arg:
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(models):
                chosen_id = models[idx]["id"]
        else:
            for m in models:
                if m["id"].lower() == arg or m["name"].lower() == arg:
                    chosen_id = m["id"]
                    break

    if not chosen_id:
        print(f"\n{BOLD}{CYAN}{'='*78}{RESET}")
        print(f"{BOLD}{YELLOW}                     AMADEUS COGNITIVE MODEL SELECTOR{RESET}")
        print(f"{BOLD}{CYAN}{'='*78}{RESET}")
        for i, m in enumerate(models, 1):
            is_active = (m["id"] == brain.model)
            marker = f"{BOLD}{GREEN}[ACTIVE]{RESET}" if is_active else "        "
            active_num = f"{BOLD}{GREEN}{i:2d}{RESET}" if is_active else f"{CYAN}{i:2d}{RESET}"
            print(f"  {active_num}. {BOLD}{m['name']:<28}{RESET} {marker} ({GRAY}{m['id']}{RESET})")
            print(f"      {GRAY}* {m['desc']}{RESET}")
        print(f"{BOLD}{CYAN}{'-'*78}{RESET}")
        print(f"{GRAY}Enter model number (1-{len(models)}), model ID, or press ENTER to cancel:{RESET}")

        prompt_str = f"{BOLD}{YELLOW}Select Model > {RESET}"
        choice, _ = await read_input_line(prompt_str)
        choice = choice.strip()
        if not choice:
            print(f"{GRAY}[*] Model selection cancelled. Active model remains: {brain.model}{RESET}\n")
            return

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                chosen_id = models[idx]["id"]
            else:
                print(f"{RED}[!] Invalid selection number.{RESET}\n")
                return
        else:
            for m in models:
                if m["id"].lower() == choice.lower() or m["name"].lower() == choice.lower():
                    chosen_id = m["id"]
                    break
            if not chosen_id:
                chosen_id = choice

    if chosen_id:
        if chosen_id == brain.model:
            print(f"{YELLOW}[*] Model is already active: {chosen_id}{RESET}\n")
            return

        print(f"\n{YELLOW}[*] Switching cognitive engine to: {chosen_id}...{RESET}")
        try:
            await brain.switch_model(chosen_id)
            print(f"{BOLD}{GREEN}[+] Cognitive model switched to: {chosen_id}{RESET}")
            print(f"{GRAY}    [Context preserved — session ID: {brain.conversation_id}]{RESET}\n")
        except Exception as e:
            print(f"{RED}[!] Error switching model: {e}{RESET}\n")

async def handle_resume_command(brain: AmadeusBrain) -> bool:
    """Displays previous Amadeus conversations and switches active context."""
    conversations = brain.list_conversations()
    if not conversations:
        print(f"\n{YELLOW}[Kurisu]: There are no previous conversations in the archive to resume.{RESET}\n")
        return False

    print(f"\n{BOLD}{CYAN}=== Amadeus Conversation Archive ==={RESET}")
    for i, conv in enumerate(conversations, 1):
        title = conv["title"]
        last_mod = conv["last_modified"]
        turns = conv["step_count"]
        print(f"  {CYAN}[{i}]{RESET} {BOLD}{title}{RESET} | Date: {last_mod} | Turns: {turns}")
    print(f"{BOLD}{CYAN}===================================={RESET}")

    prompt_str = f"{BOLD}Enter number to resume (or press Enter to cancel): {RESET}"
    choice, _ = await read_input_line(prompt_str)
    choice = choice.strip()

    if not choice:
        print(f"{GRAY}[*] Resumption canceled.{RESET}\n")
        return False

    if not choice.isdigit():
        print(f"{RED}[!] Invalid number.{RESET}\n")
        return False

    idx = int(choice) - 1
    if not (0 <= idx < len(conversations)):
        print(f"{RED}[!] Invalid conversation number.{RESET}\n")
        return False

    chosen_conv = conversations[idx]
    chosen_id = chosen_conv["id"]
    chosen_title = chosen_conv["title"]

    if chosen_id == brain.conversation_id:
        print(f"{YELLOW}[*] Conversation is already active.{RESET}\n")
        return False

    print(f"\n{YELLOW}[*] Resuming conversation: {chosen_title}...{RESET}")
    try:
        await brain.switch_conversation(chosen_id)
        print(f"{BOLD}{GREEN}[+] Resumed: {chosen_title}{RESET}\n")

        # Display previous chat history from the resumed conversation
        history_messages = brain.get_conversation_history(chosen_id)
        if history_messages:
            print(f"{GRAY}{'─' * 60}{RESET}")
            for msg in history_messages:
                if msg["role"] == "user":
                    print(f"{BOLD}{GREEN}You > {RESET}{msg['content']}\n")
                elif msg["role"] == "model":
                    print(f"{BOLD}{MAGENTA}Amadeus > {RESET}{msg['content']}\n")
            print(f"{GRAY}{'─' * 60}{RESET}\n")

        return True
    except Exception as e:
        print(f"{RED}[!] Error resuming conversation: {e}{RESET}\n")
        return False

async def main():
    if os.name == 'nt':
        os.system('')

    # Step 1: Ensure Windows microphone is unmuted
    was_muted, mic_level = ensure_microphone_unmuted()
    if was_muted:
        print(f"{GREEN}[+] Windows microphone was muted. Automatically unmuted & volume set to {int(mic_level*100)}%.{RESET}")
    else:
        print(f"{GREEN}[+] Windows microphone verified (Unmuted, Volume: {int(mic_level*100)}%).{RESET}")

    # Step 1b: Start Desktop WebSocket Bridge
    try:
        from amadeus_ws import start_server as start_ws_server
        start_ws_server()
        print(f"{GREEN}[+] Desktop WebSocket Bridge listening on ws://127.0.0.1:8765{RESET}")
    except Exception as e:
        print(f"{GRAY}[*] Desktop WebSocket Bridge not started: {e}{RESET}")

    # Step 2: Initialize Brain & Voice Engine (Defaulting to gemini-3.8-flash-low for conversational responsiveness)
    brain = AmadeusBrain(model="gemini-3.8-flash-low")
    await brain.start()

    print(get_banner(brain.model))
    print(f"{YELLOW}[*] Initializing Amadeus Subsystems...{RESET}")

    tts = AmadeusTTS()
    tts.initialize()

    stt = SenseVoiceSTT()
    ptt = AmadeusPushToTalk(stt)

    # Bind active Brain, TTS, PTT, and Main Event Loop to WebSocket Bridge
    try:
        from amadeus_ws import attach_subsystems
        attach_subsystems(brain=brain, tts=tts, ptt=ptt, loop=asyncio.get_running_loop())
    except Exception as e:
        print(f"[!] Warning attaching subsystems: {e}")

    voice_enabled = True
    command_history = []

    print(f"{GREEN}[+] Amadeus Brain online ({brain.model}). Push-to-Talk [CAPS LOCK] ready.{RESET}\n")

    is_first_message = True

    try:
        while True:
            prompt_str = f"{BOLD}{GREEN}You [Hold CAPS LOCK to Speak | Or type text] > {RESET}"
            user_input, user_emotion = await read_input_line(
                prompt_str,
                ptt=ptt,
                tts=tts,
                history=command_history
            )
            if not user_input:
                continue

            cmd = user_input.strip()

            # Command: Change or select cognitive model
            if cmd.lower().startswith("/model"):
                parts = cmd.split(maxsplit=1)
                arg = parts[1] if len(parts) > 1 else ""
                await prompt_model_selector(brain, arg)
                continue

            # Command: Resume or switch conversation
            if cmd.lower() == "/resume":
                resumed = await handle_resume_command(brain)
                if resumed:
                    is_first_message = False
                continue

            # Command: Silence current speech
            if cmd.lower() == "/stop":
                tts.stop()
                print(f"{YELLOW}[*] Voice playback halted.{RESET}")
                continue

            # Command: Toggle Voice Output Mute
            if cmd.lower() == "/mute":
                voice_enabled = not voice_enabled
                state = "DISABLED" if not voice_enabled else "ENABLED"
                print(f"{YELLOW}[*] Audio speech output {state}.{RESET}")
                continue

            # Command: Quit
            if cmd.lower() in ("/exit", "/quit"):
                print(f"{YELLOW}[*] Disconnecting Amadeus...{RESET}")
                break

            # Command: /clear (Silent session reset)
            if cmd.lower() == "/clear":
                tts.stop()
                await brain.reset_session()
                is_first_message = True
                os.system('cls' if os.name == 'nt' else 'clear')
                print(get_banner(brain.model))
                continue

            # Stop any ongoing speech before starting a new turn
            tts.stop()

            print(f"{BOLD}{MAGENTA}Amadeus > {RESET}", end="", flush=True)

            first_token_received = False
            full_response_text = ""

            # Hydrate context with metadata tags (Acoustic Tone, Session Milestones)
            meta_tags = []
            if user_emotion and user_emotion != "EMO_UNKNOWN":
                meta_tags.append(f"Acoustic Tone: {user_emotion}")
            if is_first_message:
                meta_tags.append("First message this session")
                is_first_message = False

            if meta_tags:
                prompt_to_send = f"[{' | '.join(meta_tags)}] {user_input}"
            else:
                prompt_to_send = user_input

            tts.begin_turn()
            try:
                from amadeus_ws import broadcast_sync
                broadcast_sync({"type": "user_msg", "text": user_input})
                broadcast_sync({"type": "stream_start"})
            except Exception:
                pass

            async for event in brain.chat(prompt_to_send):
                etype = event.get("type")

                if etype == "thought":
                    thought_delta = event.get("text", "")
                    if thought_delta:
                        try:
                            broadcast_sync({"type": "thought", "text": thought_delta})
                        except Exception:
                            pass

                elif etype == "tool":
                    tool_data = event.get("classified") or event.get("details", {})
                    try:
                        if isinstance(tool_data, list):
                            for item in tool_data:
                                broadcast_sync({"type": "tool_event", "tool": item})
                        else:
                            broadcast_sync({"type": "tool_event", "tool": tool_data})
                    except Exception:
                        pass

                elif etype == "tool_demoted":
                    try:
                        broadcast_sync(event)
                    except Exception:
                        pass

                elif etype == "agent_response":
                    token = event.get("text_delta", "")
                    sys.stdout.write(token)
                    sys.stdout.flush()
                    first_token_received = True
                    try:
                        broadcast_sync({"type": "token", "token": token})
                    except Exception:
                        pass
                    # Stream straight into TTS: speech starts at the first sentence/clause
                    if voice_enabled:
                        tts.feed(token)

                elif etype == "done":
                    full_response_text = event.get("response", "")
                    done_thought = event.get("thought", "")
                    if not first_token_received:
                        sys.stdout.write(full_response_text)
                        sys.stdout.flush()
                        try:
                            broadcast_sync({"type": "token", "token": full_response_text})
                        except Exception:
                            pass
                        if voice_enabled:
                            tts.feed(full_response_text)
                    print()
                    try:
                        broadcast_sync({"type": "stream_end", "response": full_response_text, "thought": done_thought})
                        if hasattr(brain, "get_conversation_subagents"):
                            broadcast_sync({"type": "session_subagents", "subagents": brain.get_conversation_subagents()})
                        if hasattr(brain, "get_conversation_tasks"):
                            broadcast_sync({"type": "session_tasks", "tasks": brain.get_conversation_tasks()})
                        if hasattr(brain, "get_conversation_commands"):
                            broadcast_sync({"type": "session_commands", "commands": brain.get_conversation_commands()})
                        if hasattr(brain, "get_conversation_diffs"):
                            broadcast_sync({"type": "session_diffs", "diffs": brain.get_conversation_diffs()})
                    except Exception:
                        pass

            # Speak whatever tail is left (last sentence has no trailing whitespace yet)
            if voice_enabled:
                tts.flush()

    finally:
        ptt.close()
        tts.close()
        await brain.close()
        print(f"{RED}[-] Amadeus session terminated.{RESET}")

if __name__ == "__main__":
    asyncio.run(main())
