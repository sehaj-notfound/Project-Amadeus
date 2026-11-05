"""
Standalone English Kurisu Inference Engine (Epoch 103)
======================================================
Self-contained runner for the finalised English voice engine.

Usage:
    python infer.py "Hello world!" --emotion Neutral --output output.wav
    python infer.py "Why are you looking at me like that?" --emotion Teasing --style_weight 0.70
"""

import sys
import os
import re
import argparse
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Tuple

import numpy as np
import scipy.signal
import soundfile as sf
import torch

from style_bert_vits2.constants import Languages
from style_bert_vits2.nlp import bert_models
from style_bert_vits2.tts_model import TTSModel
from style_bert_vits2.models.infer import infer

ENGINE_DIR = Path(__file__).resolve().parent

# --- 1. Text Normalizer ---
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

def normalize_text(text: str) -> str:
    for acr, exp in TECHNICAL_ACRONYMS.items():
        text = re.sub(r"\b" + re.escape(acr) + r"\b", exp, text)
    for pat, rep in COMPOUND_CORRECTIONS.items():
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()

# --- 2. Anti-Metallic DSP Filter ---
def apply_anti_metallic_filter(audio: np.ndarray, sr: int = 44100) -> np.ndarray:
    if len(audio) == 0:
        return audio
    sig = audio.astype(np.float64)
    # Stage A: 6.8 - 10.0 kHz attenuation (-5.0 dB)
    sos_bp = scipy.signal.butter(2, [6800, 10000], btype="bandpass", fs=sr, output="sos")
    band = scipy.signal.sosfiltfilt(sos_bp, sig)
    atten = 1.0 - (10.0 ** (-5.0 / 20.0))
    stage_a = sig - atten * band
    # Stage B: 11.0 kHz Butterworth brickwall lowpass (effective 6th order)
    sos_lp = scipy.signal.butter(3, 11000, btype="low", fs=sr, output="sos")
    stage_b = scipy.signal.sosfiltfilt(sos_lp, stage_a)
    return stage_b.astype(np.float32)

# --- 3. Bell-Curve Truncation ---
@contextmanager
def truncated_normal(clamp_bound: float = 1.25, seed: int = 42):
    torch.manual_seed(seed)
    np.random.seed(seed)
    orig_randn = torch.randn
    orig_randn_like = torch.randn_like
    def c_randn(*a, **kw): return torch.clamp(orig_randn(*a, **kw), -clamp_bound, clamp_bound)
    def c_randn_like(*a, **kw): return torch.clamp(orig_randn_like(*a, **kw), -clamp_bound, clamp_bound)
    torch.randn = c_randn
    torch.randn_like = c_randn_like
    try:
        yield
    finally:
        torch.randn = orig_randn
        torch.randn_like = orig_randn_like

# --- 4. Hypersphere Projection ---
def get_style_vector(style_vectors, style2id, emotion="Neutral", style_weight=0.70):
    id_0 = style2id["Neutral"]
    v_0 = style_vectors[id_0].astype(np.float32)
    r_0 = float(np.linalg.norm(v_0))
    if emotion == "Neutral" or style_weight <= 0:
        return v_0
    id_e = style2id[emotion]
    v_e = style_vectors[id_e].astype(np.float32)
    r_e = float(np.linalg.norm(v_e))
    u_0 = v_0 / (r_0 + 1e-12)
    u_e = v_e / (r_e + 1e-12)
    w = float(style_weight)
    d = (1.0 - w) * u_0 + w * u_e
    u_d = d / (np.linalg.norm(d) + 1e-12)
    r_target = (1.0 - w) * r_0 + w * min(r_e, r_0)
    return (u_d * r_target).astype(np.float32)

# --- 5. Main Engine Class ---
class EnglishKurisuVoice:
    def __init__(self, device: str = "cpu"):
        bert_models.load_tokenizer(Languages.EN, "microsoft/deberta-v3-large")
        deberta = bert_models.load_model(Languages.EN, "microsoft/deberta-v3-large")
        deberta.to(torch.float32)

        model_path = ENGINE_DIR / "Kurisu_EN_e103_s21000.safetensors"
        config_path = ENGINE_DIR / "config.json"
        style_path = ENGINE_DIR / "style_vectors.npy"

        self.model = TTSModel(model_path=model_path, config_path=config_path, style_vec_path=style_path, device=device)
        self.model.load()
        self.style_vectors = np.load(style_path)
        self.style2id = self.model.style2id
        self.sr = self.model.hyper_parameters.data.sampling_rate
        self.device = device

    def infer(self, text: str, emotion: str = "Neutral", style_weight: float = 0.70,
              noise_scale: float = 0.50, noise_scale_w: float = 0.80, sdp_ratio: float = 0.15,
              length_scale: float = 1.0, seed: int = 42, apply_dsp: bool = True):
        norm_text = normalize_text(text)
        style_vec = get_style_vector(self.style_vectors, self.style2id, emotion, style_weight)
        with truncated_normal(clamp_bound=1.25, seed=seed):
            with torch.inference_mode():
                audio = infer(
                    text=norm_text, style_vec=style_vec, sdp_ratio=sdp_ratio,
                    noise_scale=noise_scale, noise_scale_w=noise_scale_w, length_scale=length_scale,
                    sid=0, language=Languages.EN, hps=self.model.hyper_parameters,
                    net_g=self.model._TTSModel__net_g, device=self.device
                )
        if apply_dsp:
            audio = apply_anti_metallic_filter(audio, sr=self.sr)
        return self.sr, audio

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="English Kurisu Voice Inference")
    parser.add_argument("text", type=str, help="Text to speak")
    parser.add_argument("--emotion", type=str, default="Neutral", choices=["Neutral", "Angry", "Tsundere", "Teasing", "Warm", "Serious", "Surprised"])
    parser.add_argument("--style_weight", type=float, default=0.70)
    parser.add_argument("--noise_scale", type=float, default=0.50)
    parser.add_argument("--noise_scale_w", type=float, default=0.80)
    parser.add_argument("--output", type=str, default="output.wav")
    args = parser.parse_args()

    engine = EnglishKurisuVoice()
    sr, audio = engine.infer(args.text, emotion=args.emotion, style_weight=args.style_weight,
                             noise_scale=args.noise_scale, noise_scale_w=args.noise_scale_w)
    sf.write(args.output, audio, sr)
    print(f"[+] Audio successfully saved to: {args.output}")
