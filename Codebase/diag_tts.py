"""
Quick diagnostic: hits GPT-SoVITS /tts directly and tries to play the response.
Tells us exactly where the chain breaks.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import io
import requests
import soundfile as sf
import sounddevice as sd
import numpy as np

URL = "http://127.0.0.1:9880/tts"

print("=== Step 1: Health check ===")
try:
    r = requests.get("http://127.0.0.1:9880/health", timeout=3)
    print(f"  Status: {r.status_code} — {r.text[:80]}")
except Exception as e:
    print(f"  FAILED: {e}")
    sys.exit(1)

print()
print("=== Step 2: Synthesis request ===")
params = {"text": "I am online. Can you hear me now?", "emotion": "neutral", "style": "thoughtful_normal", "speed": 1.0}
print(f"  Params: {params}")
try:
    r = requests.get(URL, params=params, timeout=30)
    print(f"  HTTP Status : {r.status_code}")
    print(f"  Content-Type: {r.headers.get('content-type', 'unknown')}")
    print(f"  Content size: {len(r.content)} bytes")
    if r.status_code != 200:
        print(f"  Error body: {r.text[:200]}")
        sys.exit(1)
    if len(r.content) < 100:
        print(f"  WARNING: response too small — likely an error payload, not audio")
        print(f"  Raw: {r.content}")
        sys.exit(1)
except Exception as e:
    print(f"  FAILED: {e}")
    sys.exit(1)

print()
print("=== Step 3: Parse WAV ===")
try:
    audio, sr = sf.read(io.BytesIO(r.content), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    print(f"  Sample rate : {sr} Hz")
    print(f"  Duration    : {len(audio)/sr:.2f}s  ({len(audio)} samples)")
    print(f"  Shape       : {audio.shape}")
    print(f"  Max amplitude: {np.max(np.abs(audio)):.4f}")
except Exception as e:
    print(f"  FAILED to parse WAV: {e}")
    sys.exit(1)

print()
print("=== Step 4: Playback ===")
print(f"  Output devices available:")
for d in sd.query_devices():
    if d["max_output_channels"] > 0:
        print(f"    [{d['index']:2d}] {d['name']} (out ch: {d['max_output_channels']})")

print(f"\n  Default output: {sd.query_devices(sd.default.device[1])['name']}")
print(f"  Playing at {sr} Hz...")
try:
    sd.play(audio, sr)
    sd.wait()
    print("  Playback complete. Did you hear it? (y/n): ", end="")
    ans = input().strip().lower()
    if ans == "y":
        print("\n[OK] Full pipeline working. Issue is in AmadeusTTS threading.")
    else:
        print("\n[FAIL] Sounddevice playback silent — wrong output device or format issue.")
except Exception as e:
    print(f"  FAILED to play: {e}")
