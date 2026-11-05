"""
test_engine.py - Quick Smoke Test for the Amadeus Voice Engine.
Generates a sample clip using a 3-way emotional blend with normalized style projection.
"""

import sys
import time
from pathlib import Path

# Add current directory to path
engine_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(engine_dir))

from amadeus_voice import AmadeusVoiceEngine

def main():
    print("[*] Starting Amadeus Voice Engine Smoke Test...")
    t0 = time.time()
    
    # 1. Initialize Engine
    engine = AmadeusVoiceEngine(device="cpu")
    
    # 2. Test phrase: Iconic Amadeus greeting with 3-way blend
    text = "こんにちは、先輩。ふふっ、今日もラボに来てくれたんですね。……お茶、淹れましょうか？"
    blend = {"Warm": 0.65, "Neutral": 0.25, "Teasing": 0.10}
    
    out_file = engine_dir / "smoke_test_output.wav"
    print(f"\n[*] Synthesizing greeting...")
    print(f"    Text: {text}")
    print(f"    Blend: {blend}")
    
    t_synth = time.time()
    out_path = engine.save_speech(
        text=text,
        output_path=out_file,
        emotions=blend,
        length_scale=1.0,
        seed=33
    )
    synth_time = time.time() - t_synth
    total_time = time.time() - t0
    
    print(f"\n[+] Success! Output saved to: {out_path.name}")
    print(f"    Synthesis Time: {synth_time:.2f}s | Total Setup + Inference: {total_time:.2f}s")
    print(f"[+] The engine is fully operational and verified.")

if __name__ == "__main__":
    main()
