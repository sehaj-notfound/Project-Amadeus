"""
Project Amadeus: Local Audio Input Subsystem
SenseVoice-Small (Sherpa-ONNX) for real-time speech-to-text,
push-to-talk audio capture with zero-latency pre-buffer,
instant barge-in, and acoustic sentiment detection.
"""

import os
import sys
import re
import time
import queue
import threading
import collections
from typing import Optional, Callable, Dict, Any, Generator, Tuple
import numpy as np
import sounddevice as sd
import sherpa_onnx
from steins_gate_lexicon import correct_transcript

def ensure_microphone_unmuted() -> Tuple[bool, float]:
    """
    Checks the default Windows recording endpoint using Core Audio API (pycaw).
    Unmutes the microphone if muted and boosts volume if low.
    Returns: (was_muted, master_volume_level)
    """
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from ctypes import cast, POINTER

        dev = AudioUtilities.GetMicrophone()
        if dev:
            interface = dev.Activate(IAudioEndpointVolume._iid_, 7, None)
            vol = cast(interface, POINTER(IAudioEndpointVolume))
            muted = vol.GetMute()
            level = vol.GetMasterVolumeLevelScalar()
            if muted:
                vol.SetMute(0, None)
            if level < 0.8:
                vol.SetMasterVolumeLevelScalar(1.0, None)
                level = 1.0
            return bool(muted), float(level)
    except Exception as e:
        pass
    return False, 1.0


def _resolve_path(rel_path: str) -> str:
    """Resolves relative file paths whether running from root, Codebase/, or external folders."""
    if os.path.exists(rel_path):
        return os.path.abspath(rel_path)
    this_dir = os.path.dirname(os.path.abspath(__file__))
    cand_local = os.path.join(this_dir, rel_path)
    if os.path.exists(cand_local):
        return cand_local
    cand_parent = os.path.join(os.path.dirname(this_dir), rel_path)
    if os.path.exists(cand_parent):
        return cand_parent
    repo_root = os.path.dirname(this_dir)
    cand_repo = os.path.join(repo_root, rel_path)
    if os.path.exists(cand_repo):
        return cand_repo
    return rel_path


class SenseVoiceSTT:
    """
    SenseVoice-Small Speech-to-Text inference engine via sherpa-onnx.
    Produces high-speed transcription with acoustic emotion & event detection.
    """

    def __init__(
        self,
        model_path: str = "models/sensevoice/model.int8.onnx",
        tokens_path: str = "models/sensevoice/tokens.txt",
        num_threads: Optional[int] = None
    ):
        model_path = _resolve_path(model_path)
        tokens_path = _resolve_path(tokens_path)
        if not os.path.exists(model_path) or not os.path.exists(tokens_path):
            raise FileNotFoundError(
                f"Missing SenseVoice assets. Ensure {model_path} and {tokens_path} exist."
            )

        if num_threads is None:
            num_threads = min(4, os.cpu_count() or 2)

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_path,
            tokens=tokens_path,
            num_threads=num_threads,
            language="en",
            use_itn=True
        )

        # Warm-up: the first decode pays one-time setup cost. Pay it at startup instead of on turn 1.
        try:
            self.transcribe(np.zeros(16000, dtype=np.float32))
        except Exception:
            pass

    def transcribe(self, audio_16k: np.ndarray) -> Dict[str, Any]:
        """
        Transcribes a 1D float32 audio waveform sampled at 16 kHz.
        Returns a dictionary with raw text, clean text, emotion, and event markers.
        """
        if len(audio_16k) == 0:
            return {"text": "", "clean_text": "", "emotion": "NEUTRAL", "event": "Speech", "lang": "en"}

        stream = self.recognizer.create_stream()
        stream.accept_waveform(16000, audio_16k.astype(np.float32))
        self.recognizer.decode_stream(stream)
        res = stream.result

        raw_text = res.text.strip()
        emotion = getattr(res, "emotion", "<|EMO_UNKNOWN|>").replace("<|", "").replace("|>", "")
        event = getattr(res, "event", "<|Speech|>").replace("<|", "").replace("|>", "")
        lang = getattr(res, "lang", "<|en|>").replace("<|", "").replace("|>", "")

        # Format readable emotion tag - strictly preserved, never run through corrector
        if emotion in ("EMO_UNKNOWN", "NEUTRAL", ""):
            emotion_clean = "NEUTRAL"
        else:
            emotion_clean = emotion.capitalize()

        # Apply proper-noun fuzzy correction from steins_gate_lexicon ONLY to spoken text
        clean_text = raw_text
        if clean_text:
            clean_text = correct_transcript(clean_text)

        return {
            "text": raw_text,
            "clean_text": clean_text,
            "emotion": emotion_clean,
            "event": event,
            "lang": lang
        }


class AmadeusPushToTalk:
    """
    Dedicated Push-to-Talk recorder with a persistent warm background stream.
    Maintains a rolling 320ms pre-buffer so opening words are never clipped
    by sound driver initialization delay (0ms latency).
    """

    def __init__(self, stt: SenseVoiceSTT, input_device: Optional[int] = None):
        self.stt = stt
        self.input_device = input_device
        self.sample_rate = 16000
        self.chunk_size = 512

        self._pre_buffer = collections.deque(maxlen=10)  # 10 chunks * 32ms = 320ms lead-in
        self._recording_buffer = []
        self._is_recording = False
        self._stream: Optional[sd.InputStream] = None
        self._start_warm_stream()

    def _start_warm_stream(self):
        """Starts the persistent warm audio stream."""
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.chunk_size,
                device=self.input_device,
                callback=self._audio_callback
            )
            self._stream.start()
        except Exception as e:
            print(f"[Warning: Failed to start warm audio stream: {e}]")

    def _audio_callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        if self._is_recording:
            self._recording_buffer.append(chunk)
        else:
            self._pre_buffer.append(chunk)

    def close(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def start_recording(self):
        """Starts recording audio buffer, capturing with rolling pre-buffer lead-in."""
        self._recording_buffer = list(self._pre_buffer)
        self._is_recording = True

    def stop_recording(self) -> Dict[str, Any]:
        """Stops recording, concatenates recorded audio, and transcribes via SenseVoiceSTT."""
        self._is_recording = False
        if not self._recording_buffer:
            return {"text": "", "clean_text": "", "emotion": "NEUTRAL", "event": "Speech", "lang": "en"}
        audio = np.concatenate(self._recording_buffer, axis=0)
        self._recording_buffer = []
        return self.stt.transcribe(audio)

    def record_while_pressed(
        self,
        is_pressed_fn: Callable[[], bool],
        on_start_recording: Optional[Callable[[], None]] = None
    ) -> Dict[str, Any]:
        """
        Instantly captures audio while is_pressed_fn() returns True.
        Prepends the 320ms lead-in pre-buffer so the first syllables are fully preserved.
        """
        if on_start_recording:
            on_start_recording()

        # Prepend the lead-in buffer to capture speech from t=0
        self._recording_buffer = list(self._pre_buffer)
        self._is_recording = True
        rms_peak = 0.0

        while is_pressed_fn():
            time.sleep(0.02)
            if self._recording_buffer:
                recent = self._recording_buffer[-1]
                rms = float(np.sqrt(np.mean(recent ** 2)))
                rms_peak = max(rms_peak, rms)
                # Render live 10-segment VU meter
                bars = int(min(1.0, rms * 20.0) * 10)
                meter = "█" * bars + "░" * (10 - bars)
                sys.stdout.write(f"\r\033[36m[🎙️ Recording... |{meter}| Speak now (Release CAPS LOCK when done)]\033[0m")
                sys.stdout.flush()

        self._is_recording = False
        t_release = time.perf_counter()
        sys.stdout.write("\r\033[33m[Transcribing audio with SenseVoice...]                           \033[0m\n")
        sys.stdout.flush()

        if not self._recording_buffer:
            return {"text": "", "clean_text": "", "emotion": "NEUTRAL", "event": "Speech", "lang": "en"}

        audio_full = np.concatenate(self._recording_buffer, axis=0)
        self._recording_buffer.clear()

        # Check if the audio was silent / uncaptured
        if rms_peak < 0.0001:
            print("\033[33m[Notice: Microphone input level was zero. Check physical mic mute key (Fn+F4) or privacy settings]\033[0m")

        result = self.stt.transcribe(audio_full)
        result["t_release"] = t_release
        return result
