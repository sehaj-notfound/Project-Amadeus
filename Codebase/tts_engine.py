"""
Project Amadeus: Local Audio Subsystem & Dual-Stream TTS Engine (Phase 2.8 — Style-Bert-VITS2)
=============================================================================================

Architecture:
  Dual-Stream Sentence Pipelining:
    LLM stream deltas → DualStreamStreamer → SentenceUnit(blend, ja_text, en_text)
                                                  │
                            Focus Gating: is_amadeus_focused()
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          ▼                                               ▼
               Amadeus in Foreground                           Amadeus in Background
             Japanese Voice Engine (SBV2)                    English Voice Engine (SBV2)
           (Kurisu VN Golden Checkpoint e63)              (Kurisu Dub Golden Checkpoint e103)
                          │                                               │
                          └───────────────────────┬───────────────────────┘
                                                  ▼
                                      Audio Queue (_audio_q)
                                                  │
                                     Persistent 44.1 kHz Stream
                                                  │
                                    Windows Default Audio Device
"""

import abc
import ctypes
from ctypes import wintypes
import os

# Load persisted hardware configuration (threads, device) determined once during installation
try:
    from hardware_detector import get_or_create_hardware_config
except ImportError:
    try:
        from Codebase.hardware_detector import get_or_create_hardware_config
    except ImportError:
        def get_or_create_hardware_config():
            return {"threads": 6, "device": "cpu"}

_HW_CONFIG = get_or_create_hardware_config()
_NUM_THREADS = _HW_CONFIG["threads"]
_TARGET_DEVICE = _HW_CONFIG["device"]
_JA_ONLY = _HW_CONFIG["ja_only"]

# Universal thread scheduling: applied before importing torch/numpy
os.environ["OMP_NUM_THREADS"] = str(_NUM_THREADS)
os.environ["MKL_NUM_THREADS"] = str(_NUM_THREADS)

import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal
import sounddevice as sd
import soundfile as sf
import torch

torch.set_num_threads(_NUM_THREADS)

# Add engine directories to sys.path dynamically for clean imports
_ROOT_DIR = Path(__file__).resolve().parent.parent
_JP_ENGINE_DIR = _ROOT_DIR / "finalised-japanese-engine"
_EN_ENGINE_DIR = _ROOT_DIR / "finalised-english-engine"

if str(_JP_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_JP_ENGINE_DIR))
if str(_EN_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_EN_ENGINE_DIR))

try:
    from amadeus_ws import broadcast_sync
except ImportError:
    try:
        from Codebase.amadeus_ws import broadcast_sync
    except ImportError:
        def broadcast_sync(payload):
            pass

from amadeus_voice import AmadeusVoiceEngine
from infer import EnglishKurisuVoice, truncated_normal
from style_bert_vits2.constants import Languages
from style_bert_vits2.models.infer import infer as sbv2_infer


# ─────────────────────────────────────────────────────────────────────────────
# 1. Win32 Audio Device Resolver & Hot-Swap Watcher
# ─────────────────────────────────────────────────────────────────────────────

def get_windows_default_device_info() -> Tuple[str, str]:
    """
    Directly queries the Windows Core Audio COM API for the active default playback device.
    Returns (device_id, friendly_name), or ("", "") on error.
    """
    if sys.platform != "win32":
        return "", ""
    try:
        from pycaw.pycaw import AudioUtilities
        spk = AudioUtilities.GetSpeakers()
        if spk:
            return str(spk.id), str(spk.FriendlyName)
    except Exception:
        pass
    return "", ""


def get_default_output_device_index(target_friendly_name: Optional[str] = None) -> Optional[int]:
    """
    Returns the sounddevice output index corresponding to the current Windows default audio device.
    Matches the friendly name against sounddevice MME/DirectSound host APIs.
    """
    try:
        if not target_friendly_name:
            _, target_friendly_name = get_windows_default_device_info()

        if target_friendly_name:
            clean_target = target_friendly_name.lower().strip()
            # 1. First priority: Search MME devices for matching friendly name
            for idx, d in enumerate(sd.query_devices()):
                if d.get("max_output_channels", 0) > 0 and clean_target in d["name"].lower():
                    api_name = sd.query_hostapis()[d["hostapi"]]["name"]
                    if "mme" in api_name.lower():
                        return idx

            # 2. Second priority: Search any other host API (DirectSound, etc.)
            for idx, d in enumerate(sd.query_devices()):
                if d.get("max_output_channels", 0) > 0 and clean_target in d["name"].lower():
                    return idx

        # Fallback to MME default_output_device
        for api in sd.query_hostapis():
            if "mme" in api["name"].lower():
                idx = api.get("default_output_device", -1)
                if idx >= 0:
                    return int(idx)

        # Ultimate fallback
        return sd.default.device[1]  # type: ignore[index]
    except Exception:
        return None


def _get_default_output_name() -> str:
    """Returns a stable string key for the current default output device name."""
    try:
        _, friendly = get_windows_default_device_info()
        if friendly:
            return friendly
        idx = get_default_output_device_index()
        if idx is not None:
            return sd.query_devices(idx)["name"]
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Window Focus Detection (Sentence-Boundary Language Gating)
# ─────────────────────────────────────────────────────────────────────────────

_gui_focused: bool = False

def set_gui_focused(focused: bool) -> None:
    global _gui_focused
    _gui_focused = bool(focused)

def is_amadeus_focused() -> bool:
    """
    Returns True if either the Amadeus Desktop GUI or the Amadeus terminal console
    is currently in the foreground.
    Used at sentence boundaries to route speech to Japanese (focused) or English (background).
    """
    global _gui_focused
    if _gui_focused:
        return True

    if sys.platform != "win32":
        return True
    try:
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if not fg:
            return False

        kernel32 = ctypes.windll.kernel32
        console_hwnd = kernel32.GetConsoleWindow()
        if console_hwnd:
            if fg == console_hwnd:
                return True
            GA_ROOTOWNER = 3
            if fg == user32.GetAncestor(console_hwnd, GA_ROOTOWNER):
                return True

        length = user32.GetWindowTextLengthW(fg)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(fg, buff, length + 1)
            title = buff.value
            if "Project Amadeus" in title or "Amadeus" in title:
                return True
    except Exception:
        pass
    return False

is_terminal_focused = is_amadeus_focused


# ─────────────────────────────────────────────────────────────────────────────
# 3. Canonical 7-Emotion Specification & Blending Math
# ─────────────────────────────────────────────────────────────────────────────

VALID_EMOTIONS = {
    "Neutral",
    "Tsundere",
    "Teasing",
    "Angry",
    "Warm",
    "Serious",
    "Surprised",
    "Flustered",
    "Distressed",
}

DEFAULT_EMOTION_BLEND = {"Neutral": 1.0}

# Acoustic style expansions for composite emotions into the 7 base styles supported by SBV2
ACOUSTIC_EXPANSION_MAP = {
    "Flustered": {"Tsundere": 0.5, "Warm": 0.5},
    "Distressed": {"Serious": 0.7, "Surprised": 0.3},
}


def expand_to_acoustic_blend(blend: Dict[str, float]) -> Dict[str, float]:
    """Expands composite emotions into the 7 canonical base styles trained in SBV2."""
    expanded: Dict[str, float] = {}
    for emo, weight in blend.items():
        if emo in ACOUSTIC_EXPANSION_MAP:
            for base_emo, base_weight in ACOUSTIC_EXPANSION_MAP[emo].items():
                expanded[base_emo] = expanded.get(base_emo, 0.0) + weight * base_weight
        else:
            expanded[emo] = expanded.get(emo, 0.0) + weight
    total = sum(expanded.values())
    if total > 0:
        return {k: v / total for k, v in expanded.items()}
    return {"Neutral": 1.0}


def parse_emotion_blend(tag_str: Optional[str]) -> Optional[Dict[str, float]]:
    """
    Parses an emotion tag into a normalized dictionary of weights across the 7 base emotions.
    Returns None if no recognized emotions were present in the tag.
    
    Supported Formats:
      - Single shorthand: "{tsundere}" -> {"Tsundere": 1.0}
      - Composite blend:  "{warm: 0.7, neutral: 0.3}" -> {"Warm": 0.7, "Neutral": 0.3}
      - Equals notation:  "{teasing=0.6, neutral=0.4}" -> {"Teasing": 0.6, "Neutral": 0.4}
    """
    if not tag_str:
        return None

    clean = tag_str.strip().strip("{}").strip()
    if not clean:
        return None

    # Shorthand notation (no delimiters)
    if not any(c in clean for c in (":", "=", ",")):
        name = clean.strip().capitalize()
        if name in VALID_EMOTIONS:
            return {name: 1.0}
        return None

    blend: Dict[str, float] = {}
    parts = clean.split(",")
    for part in parts:
        if ":" in part:
            k, v = part.split(":", 1)
        elif "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, "1.0"

        name = k.strip().capitalize()
        if name in VALID_EMOTIONS:
            try:
                weight = float(v.strip())
                if weight > 0:
                    blend[name] = weight
            except ValueError:
                pass

    if not blend:
        return None

    # Normalize weights so sum equals 1.0
    total = sum(blend.values())
    if total > 0:
        return {k: v / total for k, v in blend.items()}
    return None


_CURLY_TAG_RE = re.compile(r"\{([^}]+)\}")


def scan_latest_emotion_blend(text: str) -> Optional[Dict[str, float]]:
    """
    Scans text for any {...} blocks (e.g. {neutral}, {tsundere:0.7, neutral:0.3})
    and returns the parsed blend of the latest valid emotion tag found, or None.
    """
    latest = None
    for m in _CURLY_TAG_RE.finditer(text):
        res = parse_emotion_blend(m.group(0))
        if res is not None:
            latest = res
    return latest


# ─────────────────────────────────────────────────────────────────────────────
# 4. Japanese & English Pronunciation & Lore Text Normalization Layer
# ─────────────────────────────────────────────────────────────────────────────

# Dictionary for correcting Japanese Steins;Gate lore terms / proper nouns for OpenJTalk/MeCab.
CORRECT_TERMS_FOR_JAPANESE: Dict[str, str] = {
    "牧瀬紅莉栖": "牧瀬クリス",
    "紅莉栖": "クリス",
    "鳳凰院凶真": "ほうおういんきょうま",
    "凶真": "きょうま",
    "阿万音鈴羽": "あまねすずは",
    "阿万音": "あまね",
    "桐生萌郁": "桐生もえか",
    "萌郁": "もえか",
    "中鉢博士": "なかばちはかせ",
    "中鉢": "なかばち",
    "秋葉留未穂": "あきはるみほ",
    "天王寺裕吾": "天王寺ゆうご",
    "天王寺綯": "天王寺なえ",
    "綯": "なえ",
    "Dr. Pepper": "ドクターペッパー",
    "Dr.Pepper": "ドクターペッパー",
    "Dr Pepper": "ドクターペッパー",
    "Doctor Pepper": "ドクターペッパー",
}


def apply_japanese_normalization(text: str) -> str:
    """Applies canonical Steins;Gate lore pronunciation overrides for Japanese speech."""
    for written, spoken in CORRECT_TERMS_FOR_JAPANESE.items():
        text = text.replace(written, spoken)
    return text


# Dictionary for correcting Japanese lore terms / proper nouns when spoken by the English voice engine.
# Initially empty as instructed; add "Written Term": "Phonetic Spoken Replacement".
CORRECT_TERMS_FOR_ENGLISH: Dict[str, str] = {
    # e.g. "Makise Kurisu": "Makise Kurisoo",
}

TECHNICAL_ACRONYMS = {
    "CPU": "C P U", "GPU": "G P U", "RAM": "R A M", "VRAM": "V R A M",
    "API": "A P I", "APIs": "A P I s", "CLI": "C L I", "GUI": "G U I",
    "LLM": "L L M", "LLMs": "L L M s", "AI": "A I", "UI": "U I",
    "URL": "U R L", "URLs": "U R L s", "SQL": "S Q L", "IBN": "I B N",
    "CRT": "C R T", "SERN": "S E R N", "OS": "O S", "TTS": "T T S",
}

COMPOUND_CORRECTIONS = {
    r"\bsemicolon\b": "semi colon", r"\bsemicolons\b": "semi colons",
    r"\bsubdirectory\b": "sub directory", r"\bsubdirectories\b": "sub directories",
    r"\bpretrained\b": "pre trained", r"\bdataset\b": "data set",
    r"\bdatasets\b": "data sets", r"\bcodebase\b": "code base",
    r"\bcodebases\b": "code bases", r"\bruntime\b": "run time",
    r"\bworldline\b": "world line", r"\btimeleap\b": "time leap"
}


def apply_english_normalization(text: str) -> str:
    """Applies technical acronym expansion, compound corrections, and Japanese lore overrides."""
    for written, spoken in CORRECT_TERMS_FOR_ENGLISH.items():
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(written)}(?![A-Za-z0-9_])"
        text = re.sub(pattern, spoken, text, flags=re.IGNORECASE)

    for acr, exp in TECHNICAL_ACRONYMS.items():
        text = re.sub(r"\b" + re.escape(acr) + r"\b", exp, text)

    for pat, rep in COMPOUND_CORRECTIONS.items():
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    return re.sub(r"\s+", " ", text).strip()


def apply_anti_metallic_filter(audio: np.ndarray, sr: int = 44100) -> np.ndarray:
    """Two-stage zero-phase anti-metallic filter for the English voice model."""
    if len(audio) == 0:
        return audio
    sig = audio.astype(np.float64)
    # Stage A: 6.8 - 10.0 kHz attenuation (-5.0 dB)
    sos_bp = scipy.signal.butter(2, [6800, 10000], btype="bandpass", fs=sr, output="sos")
    band = scipy.signal.sosfiltfilt(sos_bp, sig)
    atten = 1.0 - (10.0 ** (-5.0 / 20.0))
    stage_a = sig - atten * band
    # Stage B: 11.0 kHz Butterworth brickwall lowpass (6th order)
    sos_lp = scipy.signal.butter(3, 11000, btype="low", fs=sr, output="sos")
    stage_b = scipy.signal.sosfiltfilt(sos_lp, stage_a)
    return stage_b.astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Dual-Stream Token Parser & Sentence Demuxer
# ─────────────────────────────────────────────────────────────────────────────

class SentenceUnit:
    """Represents a single atomic sentence pair emitted by the LLM."""
    def __init__(
        self,
        blend: Dict[str, float],
        ja_text: str,
        en_text: str,
        lang: Optional[str] = None,
    ):
        self.blend = blend
        self.ja_text = ja_text
        self.en_text = en_text
        self.lang = lang

    def __repr__(self) -> str:
        return (
            f"<SentenceUnit blend={self.blend} lang={self.lang} "
            f"JA={self.ja_text[:20]!r} EN={self.en_text[:20]!r}>"
        )


class DualStreamDemuxer:
    """
    Streams text deltas from the LLM, buffering until closing delimiters confirm
    a complete sentence block:
        {emotion_blend}[JA: <Japanese sentence>][EN: <English sentence>]
        
    Code blocks (```lang ... ```) are kept strictly outside spoken audio and bypassed.
    Any emotion tag in curly braces {...} outside conversational brackets updates the active emotion.
    When focused, eagerly triggers synthesis on [JA: ...] as soon as [EN: appears without waiting for subtitles.
    """

    _DUAL_STREAM_RE = re.compile(
        r"(\{[^}]+\})?\s*\[JA:\s*([\s\S]*?)\]\s*\[EN:\s*([\s\S]*?)(?<!\\)\](?!\()"
    )
    # Eager Japanese trigger: Japanese bracket is considered closed ONLY when followed by [EN:
    _JA_FOLLOWED_BY_EN_RE = re.compile(
        r"(\{[^}]+\})?\s*\[JA:\s*([\s\S]*?)(?<!\\)\](?!\()\s*\[EN:"
    )
    _EN_CLOSE_RE = re.compile(
        r"^\s*\[EN:\s*([\s\S]*?)(?<!\\)\](?!\()"
    )
    _OPEN_FENCE_RE = re.compile(
        r"(?:^|\n)[ \t]*(`{3,}|~{3,})[^\r\n`]*"
    )

    _LATIN_ALPHABET_JA = {
        "a": "エー", "b": "ビー", "c": "シー", "d": "ディー", "e": "イー",
        "f": "エフ", "g": "ジー", "h": "エイチ", "i": "アイ", "j": "ジェー",
        "k": "ケー", "l": "エル", "m": "エム", "n": "エヌ", "o": "オー",
        "p": "ピー", "q": "キュー", "r": "アール", "s": "エス", "t": "ティー",
        "u": "ユー", "v": "ブイ", "w": "ダブリュー", "x": "エックス", "y": "ワイ", "z": "ゼット",
    }
    _LORE_JA = {
        "sern": "セルン",
        "ibn": "アイビーエヌ",
    }
    _SPECIAL_EXT_JA = {
        "txt": "テキスト",
        "sh": "シェル",
        "cpp": "シープラスプラス",
        "bat": "バット",
    }
    _e2k_instance = None

    @classmethod
    def _to_katakana(cls, word: str) -> str:
        w_lower = word.lower()
        # 1. Steins;Gate universe lore overrides
        if w_lower in cls._LORE_JA:
            return cls._LORE_JA[w_lower]
        # 2. Special extension spoken overrides
        if w_lower in cls._SPECIAL_EXT_JA:
            return cls._SPECIAL_EXT_JA[w_lower]
        # 3. Loanword dictionary lookup (alkana)
        try:
            import alkana
            k = alkana.get_kana(w_lower)
            if k:
                return k
        except Exception:
            pass
        # 4. Consonant abbreviations / extensions without vowels (e.g. gz, py, csv, ts, html)
        # or uppercase acronyms (e.g. CPU, GPU, API, UI, OS, LLM)
        has_vowel = any(c in "aeiou" for c in w_lower)
        if not has_vowel or (len(w_lower) <= 4 and word.isupper()):
            if all(c in cls._LATIN_ALPHABET_JA for c in w_lower):
                return "".join(cls._LATIN_ALPHABET_JA[c] for c in w_lower)
        # 5. Single letters (e.g. variable n, x)
        if len(w_lower) == 1 and w_lower in cls._LATIN_ALPHABET_JA:
            return cls._LATIN_ALPHABET_JA[w_lower]
        # 6. Neural G2P fallback for arbitrary English words
        try:
            if cls._e2k_instance is None:
                import e2k
                cls._e2k_instance = e2k.C2K()
            res = cls._e2k_instance(w_lower)
            if res:
                return res
        except Exception:
            pass
        return word

    def __init__(self, focus_checker: Optional[Callable[[], bool]] = None):
        self._focus_checker = focus_checker or is_amadeus_focused
        self.reset()

    def reset(self):
        self._buf = ""
        self._in_fence = False
        self._fence_char = ""
        self._fence_len = 0
        self._waiting_for_en_close = False
        self._sticky_blend = dict(DEFAULT_EMOTION_BLEND)

    def feed(self, delta: str) -> List[SentenceUnit]:
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> List[SentenceUnit]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> List[SentenceUnit]:
        units: List[SentenceUnit] = []

        while True:
            # 1. Handle code fences: completely discard code blocks from TTS
            if self._in_fence:
                # In CommonMark, a fence of length N is closed by >= N of the same fence character on its own line
                close_pat = re.compile(
                    rf"(?:^|\n)[ \t]*{re.escape(self._fence_char)}{{{self._fence_len},}}[ \t]*(?:\r?\n|$)"
                )
                m_close = close_pat.search(self._buf)
                if not m_close:
                    if final:
                        self._buf = ""
                        self._in_fence = False
                        self._fence_char = ""
                        self._fence_len = 0
                    return units
                # Discard the entire code block from the audio buffer
                self._buf = self._buf[m_close.end():]
                self._in_fence = False
                self._fence_char = ""
                self._fence_len = 0
                continue

            # 2. If eager JA was emitted, wait for the trailing [EN: ...] subtitle bracket to close FIRST
            if self._waiting_for_en_close:
                m_en = self._EN_CLOSE_RE.search(self._buf)
                if m_en:
                    # English subtitle closed; discard it from buffer and resume normal parsing
                    self._buf = self._buf[m_en.end():]
                    self._waiting_for_en_close = False
                    continue
                else:
                    # Guard against deadlock: if buffer does not start with whitespace or [EN:,
                    # the English bracket was either already consumed, bypassed, or absent.
                    stripped = self._buf.lstrip()
                    if stripped and not stripped.startswith("[EN:"):
                        self._waiting_for_en_close = False
                        continue
                    if final:
                        self._waiting_for_en_close = False
                        extracted, remaining = self._extract_sentences(self._buf, final=True)
                        if extracted:
                            units.extend(extracted)
                            self._buf = remaining
                            continue
                        self._buf = ""
                        return units
                    return units

            # 3. Check if fence opens
            m_fence = self._OPEN_FENCE_RE.search(self._buf)
            if m_fence:
                fence_start = m_fence.start()
                if self._buf[fence_start] == "\n":
                    fence_start += 1
                elif self._buf[fence_start:fence_start+2] == "\r\n":
                    fence_start += 2

                before = self._buf[:fence_start]
                self._buf = self._buf[fence_start:]

                # Extract ALL sentences that completed before the fence
                while before.strip():
                    extracted, before_rem = self._extract_sentences(before, final=True)
                    if not extracted:
                        break
                    units.extend(extracted)
                    before = before_rem

                fence_token = m_fence.group(1)
                self._fence_char = fence_token[0]
                self._fence_len = len(fence_token)
                self._in_fence = True
                continue

            # 4. Extract complete dual-stream sentence pairs or eager JA
            extracted, remaining = self._extract_sentences(self._buf, final=final)
            if extracted:
                units.extend(extracted)
                self._buf = remaining
                continue
            else:
                self._buf = remaining
                return units

    def _extract_sentences(self, text: str, final: bool) -> Tuple[List[SentenceUnit], str]:
        units: List[SentenceUnit] = []
        focused = self._focus_checker()

        # Eager JA trigger: if window is active/focused or ja_only mode, check if JA has closed and is followed by [EN:
        if _JA_ONLY or focused:
            m_ja = self._JA_FOLLOWED_BY_EN_RE.search(text)
            if m_ja:
                if m_ja.start() > 0:
                    pre_text = text[:m_ja.start()]
                    found_blend = scan_latest_emotion_blend(pre_text)
                    if found_blend is not None:
                        self._sticky_blend = found_blend

                tag_str = m_ja.group(1)
                if tag_str:
                    matched_blend = parse_emotion_blend(tag_str)
                    if matched_blend is not None:
                        self._sticky_blend = matched_blend

                blend = dict(self._sticky_blend)
                ja_raw = m_ja.group(2).strip()
                ja_clean = self._clean_speech_text(ja_raw, lang="JA")

                # Slice text buffer to begin at [EN:
                en_start_pos = text.find("[EN:", m_ja.start())
                remaining = text[en_start_pos:]
                self._waiting_for_en_close = True

                if ja_clean:
                    units.append(
                        SentenceUnit(
                            blend=blend,
                            ja_text=ja_clean,
                            en_text="",
                            lang="JA",
                        )
                    )
                return units, remaining

        # Standard dual-stream matching (when unfocused or waiting for full [JA:...][EN:...])
        m = self._DUAL_STREAM_RE.search(text)
        if m:
            if m.start() > 0:
                pre_text = text[:m.start()]
                found_blend = scan_latest_emotion_blend(pre_text)
                if found_blend is not None:
                    self._sticky_blend = found_blend

            tag_str = m.group(1)
            if tag_str:
                matched_blend = parse_emotion_blend(tag_str)
                if matched_blend is not None:
                    self._sticky_blend = matched_blend

            blend = dict(self._sticky_blend)
            ja_raw = m.group(2).strip()
            en_raw = m.group(3).strip()

            ja_clean = self._clean_speech_text(ja_raw, lang="JA")
            en_clean = self._clean_speech_text(en_raw, lang="EN")

            remaining = text[m.end():]
            active_lang = "JA" if (_JA_ONLY or focused) else "EN"

            if ja_clean or en_clean:
                units.append(
                    SentenceUnit(
                        blend=blend,
                        ja_text=ja_clean or en_clean,
                        en_text=en_clean or ja_clean,
                        lang=active_lang,
                    )
                )
            return units, remaining

        # If final, any remaining text outside dual-stream brackets is treated as visual-only and discarded from audio synthesis
        if final and not self._waiting_for_en_close:
            return units, ""

        return units, text

    @classmethod
    def _clean_speech_text(cls, text: str, lang: str = "EN") -> str:
        """
        Cleans speech text for synthesis:
          1. Flattens markdown links [label](url) -> label and strips bare URLs.
          2. Collapses unlinked file paths (C:/Users/.../foo.py -> foo.py).
          3. Expands dots in filenames (factorial.py -> factorial dot py), leaving decimals untouched.
          4. For Japanese: transliterates any remaining English words/extensions to Katakana.
          5. Strips backticks, bold/italic asterisks, and markdown headers.
        """
        # 1. Flatten markdown links FIRST: [label](url) -> label
        text = re.sub(r"\[([^\]]+)\]\((?:file|https?):[^\)]+\)", r"\1", text)
        # Strip bare URLs if any remain
        text = re.sub(r"(?:file|https?)://[^\s\)]+", "", text)

        # 2. Extract basename from file paths (handling special characters, brackets, spaces, and absolute drives)
        def clean_dest_decorative(dest: str) -> str:
            # Strip outer decorative brackets and symbols ([foo].py -> foo.py, @foo.py -> foo.py, ~foo.py -> foo.py)
            dest = re.sub(r"^[\[\(\{@#~]+", "", dest)
            dest = re.sub(r"[\]\)\}]+(?=\.[a-zA-Z0-9_\-]+$|$)", "", dest)
            dest = re.sub(r"[\]\)\}]+$", "", dest)
            return dest

        # 2a. Quoted or outer-bracketed paths with slashes: "dir/file.ext", `dir/file.ext`
        def repl_quoted_path(m):
            open_q, inner, close_q = m.group(1), m.group(2), m.group(3)
            parts = [p for p in re.split(r"[/\\]+", inner.rstrip("/\\")) if p]
            basename = clean_dest_decorative(parts[-1]) if parts else inner
            return f"{open_q}{basename}{close_q}"

        text = re.sub(r'([`"\'\(])([^\r\n`"\'\)]*[/\\][^\r\n`"\'\)]+)([`"\'\)])', repl_quoted_path, text)

        # 2b. Absolute paths with drive letter or Unix root, possibly containing spaces/special characters
        # e.g. C:/Users/Welcome/Downloads/Project Context/[foo].py
        def repl_abs_path(m):
            full_path = m.group(0).rstrip(".,;:!?'\"`")
            parts = [p for p in re.split(r"[/\\]+", full_path.rstrip("/\\")) if p]
            trailing = m.group(0)[len(full_path):]
            dest = clean_dest_decorative(parts[-1]) if parts else full_path
            return dest + trailing

        text = re.sub(r'(?<![a-zA-Z0-9_\-\./\\])(?:[a-zA-Z]:[/\\]|/(?:Users|home|root|etc|var|tmp|opt|usr)[/\\])[^\r\n"' + "`<>|:*?]+", repl_abs_path, text)

        # 2c. Unquoted relative paths: check on-disk existence for multi-word directory names (e.g. Project Context/file.md)
        def repl_rel_disk(m):
            full = m.group(0)
            if "/" in full or "\\" in full:
                words = full.split()
                for i in range(len(words)):
                    candidate = " ".join(words[i:])
                    c_clean = candidate.rstrip(".,;:!?'\"`")
                    trailing = candidate[len(c_clean):]
                    if os.path.exists(c_clean) or os.path.isdir(os.path.dirname(c_clean)):
                        parts = [p for p in re.split(r"[/\\]+", c_clean.rstrip("/\\")) if p]
                        prefix = " ".join(words[:i])
                        dest = clean_dest_decorative(parts[-1])
                        if prefix:
                            return prefix + " " + dest + trailing
                        return dest + trailing
            parts = [p for p in re.split(r"[/\\]+", full.rstrip("/\\")) if p]
            return clean_dest_decorative(parts[-1]) if parts else full

        text = re.sub(r'(?<![a-zA-Z0-9_\-\./\\])(?:[a-zA-Z0-9_\-\.]+(?:\s+[a-zA-Z0-9_\-\.]+){0,4}[/\\])+[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+', repl_rel_disk, text)

        # 2d. Universal paths containing special folder names or bracketed segments:
        # e.g. Downloads/[Project Context]/foo.py, Downloads/@archive/#release/[foo].py
        def repl_universal_path(m):
            raw = m.group(0)
            clean = raw.rstrip(".,;:!?'\"`")
            trailing = raw[len(clean):]
            parts = [p for p in re.split(r"[/\\]+", clean.rstrip("/\\")) if p]
            if not parts:
                return raw
            dest = clean_dest_decorative(parts[-1])
            return dest + trailing

        seg = r'(?:[a-zA-Z0-9_\-\.\+~=]+|\[[^\]\r\n]+\]|\([^\)\r\n]+\)|\{[^\}\r\n]+\}|@[a-zA-Z0-9_\-]+|#[a-zA-Z0-9_\-]+|[a-zA-Z]:)'
        path_universal_re = rf'(?<![a-zA-Z0-9_\-\./\\])(?:{seg}\s*[/\\]\s*)+(?:{seg})'
        text = re.sub(path_universal_re, repl_universal_path, text)

        # 2e. Standard relative paths without spaces: Codebase/tts_engine.py -> tts_engine.py
        def repl_std_rel(m):
            full_path = m.group(0)
            parts = [p for p in re.split(r"[/\\]+", full_path.rstrip("/\\")) if p]
            return clean_dest_decorative(parts[-1]) if parts else full_path

        text = re.sub(r'(?<![a-zA-Z0-9_\-\./\\])(?:\.{1,2}[/\\]|[a-zA-Z0-9_\-\.]+[/\\])[a-zA-Z0-9_\-\./\\]+', repl_std_rel, text)

        # 3. Expand dots in filenames without touching pure decimals
        dot_word = " dot " if lang == "EN" else " ドット "
        def repl_file_dot(m):
            token = m.group(0)
            if re.fullmatch(r"\d+(?:\.\d+)+", token):
                return token
            return re.sub(r"\.", dot_word, token)

        text = re.sub(r"(?<![a-zA-Z0-9_\-/\\#])[a-zA-Z0-9_\-]+(?:\.[a-zA-Z0-9_\-]+)+(?![a-zA-Z0-9_\-])", repl_file_dot, text)

        # 4. For Japanese: apply lore term normalization and transliterate remaining English words to Katakana
        if lang == "JA":
            text = apply_japanese_normalization(text)

            def repl_en_word(m):
                w = m.group(0)
                parts = re.split(r"[_]+", w)
                katas = [cls._to_katakana(p) for p in parts if p]
                return "".join(katas)

            text = re.sub(r"[a-zA-Z]+", repl_en_word, text)

        # 5. Clean remaining markdown noise
        text = re.sub(r"`([^`]+)`", r"\1", text)               # inline backticks
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)         # bold
        text = re.sub(r"\*([^*]+)\*", r"\1", text)             # italic
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE) # headers
        return re.sub(r"\s+", " ", text).strip()




# ─────────────────────────────────────────────────────────────────────────────
# 6. Unified SBV2 Voice Backend
# ─────────────────────────────────────────────────────────────────────────────

class TTSBackend(abc.ABC):
    @abc.abstractmethod
    def initialize(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def synthesize(self, text: str, emotions: Dict[str, float], lang: str = "JA") -> Tuple[np.ndarray, int]:
        raise NotImplementedError

    @abc.abstractmethod
    def backend_name(self) -> str:
        raise NotImplementedError


class SBV2Backend(TTSBackend):
    """
    High-fidelity dual-engine Style-Bert-VITS2 speech synthesizer.
    Runs on CPU (with INT8 DeBERTa) or CUDA FP16 based on persisted hardware configuration.
    """

    SAMPLING_RATE = 44100

    def __init__(self, device: Optional[str] = None):
        self.device = device or _TARGET_DEVICE
        self.jp_engine = None
        self.en_engine = None

    def initialize(self) -> int:
        print(f"[TTS] Initializing Style-Bert-VITS2 Voice Engine (44.1 kHz, {self.device.upper()}, ja_only={_JA_ONLY})...")
        t0 = time.perf_counter()

        # 1. Initialize Japanese Engine (VN Golden Checkpoint)
        self.jp_engine = AmadeusVoiceEngine(model_dir=_JP_ENGINE_DIR / "model_assets", device=self.device)

        # 2. Initialize English Engine ONLY if not _JA_ONLY
        if not _JA_ONLY:
            self.en_engine = EnglishKurisuVoice(device=self.device)

        # 3. Dynamic INT8 Quantization of DeBERTa backbones (CPU only)
        if self.device != "cuda":
            print("[TTS] Quantizing DeBERTa backbones to INT8 for low-latency CPU inference...")
            try:
                from style_bert_vits2.nlp import bert_models
                loaded = getattr(bert_models, "__loaded_models", {})
                langs_to_quant = (Languages.JP,) if _JA_ONLY else (Languages.JP, Languages.EN)
                for lang_key in langs_to_quant:
                    if lang_key in loaded and loaded[lang_key] is not None:
                        loaded[lang_key] = torch.ao.quantization.quantize_dynamic(
                            loaded[lang_key], {torch.nn.Linear}, dtype=torch.qint8
                        )
                print("[TTS] DeBERTa INT8 dynamic quantization applied.")
            except Exception as e:
                print(f"[TTS] Warning: INT8 quantization skipped ({e}). Running in FP32.")
        else:
            print("[TTS] CUDA Mode: Tensor Core acceleration active.")

        # 4. Micro-warmup to absorb initial G2P, tokenizer, and JIT compilation latency
        print("[TTS] Running micro-warmup...")
        try:
            self.synthesize("準備完了。", {"Neutral": 1.0}, lang="JA")
            if not _JA_ONLY:
                self.synthesize("System ready.", {"Neutral": 1.0}, lang="EN")
            print("[TTS] Micro-warmup complete.")
        except Exception as e:
            print(f"[TTS] Warning: Micro-warmup encountered issue ({e}). Continuing.")

        print(f"[TTS] SBV2 engine initialized and warmed up in {time.perf_counter() - t0:.2f}s!")
        return self.SAMPLING_RATE

    def synthesize(self, text: str, emotions: Dict[str, float], lang: str = "JA") -> Tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), self.SAMPLING_RATE

        acoustic_emotions = expand_to_acoustic_blend(emotions)

        if lang.upper() == "JA":
            # Japanese Synthesis via AmadeusVoiceEngine (re-using pre-loaded weights)
            norm_text = apply_japanese_normalization(text)
            sr, audio = self.jp_engine.speak(
                text=norm_text,
                emotions=acoustic_emotions,
                length_scale=1.0,
                noise_scale=0.65,
                noise_scale_w=0.80,
                sdp_ratio=0.25,
            )
            return audio.astype(np.float32), sr

        else:
            # English Synthesis with lore term overrides and anti-metallic DSP
            norm_text = apply_english_normalization(text)
            style_vec = self._get_english_style_vector(acoustic_emotions)

            with truncated_normal(clamp_bound=1.25, seed=42):
                with torch.inference_mode():
                    audio = sbv2_infer(
                        text=norm_text,
                        style_vec=style_vec,
                        sdp_ratio=0.15,
                        noise_scale=0.50,
                        noise_scale_w=0.80,
                        length_scale=1.00,
                        sid=0,
                        language=Languages.EN,
                        hps=self.en_engine.model.hyper_parameters,
                        net_g=self.en_engine.model._TTSModel__net_g,
                        device=self.device,
                    )

            audio = apply_anti_metallic_filter(audio, sr=self.SAMPLING_RATE)
            return audio.astype(np.float32), self.SAMPLING_RATE

    def _get_english_style_vector(self, emotion_blend: Dict[str, float]) -> np.ndarray:
        """
        Calculates composite style vector for the English model with dynamic norm capping.
        Capping target norm at Neutral's norm prevents vocoder saturation.
        """
        style2id = self.en_engine.style2id
        styles_matrix = self.en_engine.style_vectors

        id_0 = style2id["Neutral"]
        v_0 = styles_matrix[id_0].astype(np.float32)
        r_0 = float(np.linalg.norm(v_0))

        # Check for pure Neutral
        total_w = sum(emotion_blend.values())
        if total_w <= 0:
            return v_0

        weights = {k: v / total_w for k, v in emotion_blend.items()}
        if weights.get("Neutral", 0) >= 0.999:
            return v_0

        # Composite unit direction and target norm
        d = np.zeros_like(v_0)
        r_target = 0.0

        for emo, w in weights.items():
            if w <= 0 or emo not in style2id:
                continue
            idx = style2id[emo]
            v_e = styles_matrix[idx].astype(np.float32)
            r_e = float(np.linalg.norm(v_e))
            u_e = v_e / (r_e + 1e-12)
            d += w * u_e
            r_target += w * min(r_e, r_0)

        norm_d = np.linalg.norm(d)
        if norm_d > 0:
            u_d = d / norm_d
        else:
            u_d = v_0 / r_0

        return (u_d * r_target).astype(np.float32)

    def backend_name(self) -> str:
        dev_tag = "CUDA FP16" if self.device == "cuda" else "CPU INT8"
        mode_tag = "Kurisu JP 44.1 kHz" if _JA_ONLY else "Kurisu JP + EN 44.1 kHz"
        return f"Style-Bert-VITS2 ({mode_tag} {dev_tag})"


# ─────────────────────────────────────────────────────────────────────────────
# 7. AmadeusTTS Controller (Public Streaming & Playback Harness)
# ─────────────────────────────────────────────────────────────────────────────

class AmadeusTTS:
    """
    Streaming TTS controller for Project Amadeus.
    Coordinates the DualStreamDemuxer, focus-gated engine switching,
    and a continuous 44.1 kHz playback queue.
    """

    def __init__(
        self,
        backend: Optional[TTSBackend] = None,
        focus_checker: Optional[Callable[[], bool]] = None,
        **kwargs,
    ):
        self._backend: Optional[TTSBackend] = backend
        self._focus_checker = focus_checker or is_amadeus_focused

        self._sr = 44100
        self._stream: Optional[sd.OutputStream] = None
        self._demuxer = DualStreamDemuxer(focus_checker=self._focus_checker)

        self._sentence_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue(maxsize=6)
        self._lock = threading.Lock()
        self._gen = 0
        self._pending = 0

        self._shutdown = threading.Event()
        self._threads: List[threading.Thread] = []
        self._device_changed = threading.Event()
        self._active_device_name: str = ""
        self._active_device_id: str = ""
        self._stream_lock = threading.Lock()
        self._muted: bool = False

    def set_muted(self, muted: bool):
        """Enables/disables live audio output (Behavior 2: Live Mute)."""
        self._muted = bool(muted)

    @property
    def is_muted(self) -> bool:
        return self._muted

    def set_gui_focused(self, focused: bool):
        set_gui_focused(focused)

    @property
    def is_speaking(self) -> bool:
        return self._pending > 0

    def _open_stream(self) -> sd.OutputStream:
        dev_id, dev_name = get_windows_default_device_info()
        device_idx = get_default_output_device_index(dev_name)
        self._active_device_id = dev_id
        if dev_name:
            self._active_device_name = dev_name
        elif device_idx is not None:
            try:
                self._active_device_name = sd.query_devices(device_idx)["name"]
            except Exception:
                self._active_device_name = "Default Output"
        else:
            self._active_device_name = "Default Output"

        print(f"[TTS] Opening audio output stream on: {self._active_device_name!r} (Index: {device_idx}, 44.1 kHz)")
        stream = sd.OutputStream(
            samplerate=self._sr,
            channels=1,
            dtype="float32",
            latency="low",
            device=device_idx,
        )
        stream.start()
        return stream

    def _reopen_stream_on_device_change(self):
        """Seamlessly hot-swaps active output stream to the new default Windows audio endpoint."""
        with self._stream_lock:
            self._device_changed.clear()
            try:
                if self._stream:
                    self._stream.stop()
                    self._stream.close()
            except Exception:
                pass
            self._stream = None

            # Re-initialize PortAudio to pick up newly added/removed Windows audio endpoints
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                print(f"[TTS] Warning re-initializing sounddevice: {e}")

            try:
                self._stream = self._open_stream()
            except Exception as e:
                print(f"[TTS] Failed to reopen stream after device change: {e}")

    def _device_watcher_loop(self):
        # Initialize COM on this worker thread for pycaw / IMMDeviceEnumerator
        if sys.platform == "win32":
            ctypes.windll.ole32.CoInitialize(None)

        poll_interval = 0.25  # 250ms polling for responsive hot-swapping
        try:
            while not self._shutdown.is_set():
                time.sleep(poll_interval)
                try:
                    current_id, current_name = get_windows_default_device_info()
                    if current_id and self._active_device_id and current_id != self._active_device_id:
                        print(f"[TTS] Default output endpoint changed: {self._active_device_name!r} → {current_name!r}")
                        self._device_changed.set()
                    elif not current_id and current_name and current_name != self._active_device_name:
                        print(f"[TTS] Default output name changed: {self._active_device_name!r} → {current_name!r}")
                        self._device_changed.set()
                except Exception:
                    pass
        finally:
            if sys.platform == "win32":
                ctypes.windll.ole32.CoUninitialize()

    def initialize(self):
        if self._backend is None:
            self._backend = SBV2Backend(device=_TARGET_DEVICE)

        self._sr = self._backend.initialize()
        self._stream = self._open_stream()

        self._shutdown.clear()
        for target in (self._synth_loop, self._play_loop, self._device_watcher_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    # ─── Public turn lifecycle API ───────────────────────────────────────────

    def begin_turn(self):
        """Call once before feeding a new reply."""
        self._demuxer.reset()

    def feed(self, delta: str):
        """Feed a streamed token delta. Complete sentence units are dispatched to synthesis."""
        units = self._demuxer.feed(delta)
        for unit in units:
            self._dispatch_unit(unit)

    def flush(self):
        """Call when the turn finishes to synthesize whatever trailing text remains."""
        units = self._demuxer.flush()
        for unit in units:
            self._dispatch_unit(unit)

    def stop(self):
        """Barge-in: immediately halts playback and purges queued sentences and audio."""
        with self._lock:
            self._gen += 1
            self._pending = 0
        self._drain_queue(self._sentence_q)
        self._drain_queue(self._audio_q)
        self._demuxer.reset()

    def close(self):
        self._shutdown.set()
        self.stop()
        for t in self._threads:
            t.join(timeout=1.0)
        with self._stream_lock:
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    # ─── Internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _drain_queue(q: queue.Queue):
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break

    def _dispatch_unit(self, unit: SentenceUnit):
        with self._lock:
            gen = self._gen
            self._pending += 1
        self._sentence_q.put((gen, unit))

    def _done(self, gen: int):
        with self._lock:
            if gen == self._gen:
                self._pending = max(0, self._pending - 1)


    def _synth_loop(self):
        """
        Pulls SentenceUnits, samples window focus at the sentence boundary,
        and synthesizes with the appropriate engine (JP if focused, EN if background).
        """
        while not self._shutdown.is_set():
            try:
                gen, unit = self._sentence_q.get(timeout=0.1)
            except queue.Empty:
                continue

            if gen != self._gen:
                continue

            # Sentence-level focus check
            if getattr(unit, "lang", None):
                active_lang = unit.lang
                text_to_speak = unit.ja_text if active_lang == "JA" else unit.en_text
            else:
                focused = self._focus_checker()
                if _JA_ONLY or focused:
                    active_lang = "JA"
                    text_to_speak = unit.ja_text
                else:
                    active_lang = "EN"
                    text_to_speak = unit.en_text

            try:
                samples, sr = self._backend.synthesize(
                    text=text_to_speak,
                    emotions=unit.blend,
                    lang=active_lang,
                )
            except Exception as e:
                print(f"[TTS Synth Error ({active_lang})] {e}")
                self._done(gen)
                continue

            while not self._shutdown.is_set() and gen == self._gen:
                try:
                    self._audio_q.put((gen, samples, unit), timeout=0.1)
                    break
                except queue.Full:
                    continue

    def _play_loop(self):
        """Streams synthesized 44.1 kHz PCM audio to the current default output device."""
        silence = np.zeros((self._sr // 100, 1), dtype=np.float32)  # 10 ms idle block
        block = 1024

        while not self._shutdown.is_set():
            if self._device_changed.is_set():
                self._reopen_stream_on_device_change()
                silence = np.zeros((self._sr // 100, 1), dtype=np.float32)

            try:
                item = self._audio_q.get(timeout=0.01)
                if len(item) == 3:
                    gen, samples, unit = item
                else:
                    gen, samples = item
                    unit = None
            except queue.Empty:
                with self._stream_lock:
                    try:
                        if self._stream:
                            self._stream.write(silence)
                    except Exception:
                        time.sleep(0.01)
                continue

            if gen != self._gen:
                continue

            # Determine dominant emotion and broadcast to Tauri desktop
            dominant = "neutral"
            if unit and getattr(unit, "blend", None):
                b = unit.blend
                if "Flustered" in b:
                    dominant = "flustered"
                elif "Distressed" in b:
                    dominant = "distressed"
                elif "Tsundere" in b and "Warm" in b and b["Tsundere"] >= 0.25 and b["Warm"] >= 0.25:
                    dominant = "flustered"
                else:
                    dominant = max(b.items(), key=lambda x: x[1])[0].lower()

            broadcast_sync({
                "type": "emotion",
                "emotion": dominant,
                "blend": unit.blend if unit else {}
            })

            sub = unit.en_text if unit and unit.en_text else (unit.ja_text if unit else "")
            if sub:
                broadcast_sync({
                    "type": "subtitle",
                    "text": sub
                })

            try:
                for i in range(0, len(samples), block):
                    if gen != self._gen or self._shutdown.is_set():
                        break

                    # Mid-sentence output device hot-swap: seamlessly switch mid-speech!
                    if self._device_changed.is_set():
                        self._reopen_stream_on_device_change()
                        silence = np.zeros((self._sr // 100, 1), dtype=np.float32)

                    chunk = samples[i:i + block]
                    if self._muted:
                        # Behavior 2 (Live Mute): output digital silence to stream, keep visemes silent
                        zero_chunk = np.zeros((len(chunk), 1), dtype=np.float32)
                        with self._stream_lock:
                            if self._stream:
                                self._stream.write(zero_chunk)
                        broadcast_sync({
                            "type": "viseme",
                            "rms": 0.0
                        })
                    else:
                        with self._stream_lock:
                            if self._stream:
                                self._stream.write(chunk.reshape(-1, 1))

                        # Real-time RMS for Viseme Lip-Flap sync
                        rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0
                        broadcast_sync({
                            "type": "viseme",
                            "rms": rms
                        })
            except Exception as e:
                print(f"[TTS Playback Error] {e}")

            # Close mouth cleanly at sentence end
            broadcast_sync({"type": "speech_end"})

            self._done(gen)