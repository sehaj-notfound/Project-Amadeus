"""
amadeus_voice.py - High-Fidelity Japanese Voice Engine for Project Amadeus.
Uses Makise Kurisu Style-Bert-VITS2 (JP-Extra) Epoch 63 Golden Checkpoint.
Features automatic Hypersphere L2 Norm Projection to eliminate metallic artifacts in emotion blending.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import numpy as np
import soundfile as sf
import torch

from style_bert_vits2.constants import Languages
from style_bert_vits2.nlp import bert_models
from style_bert_vits2.tts_model import TTSModel
from style_bert_vits2.models.infer import infer


class AmadeusVoiceEngine:
    """
    Production-ready Japanese Speech Synthesis Engine for Project Amadeus.
    """
    def __init__(self, model_dir: Optional[Union[str, Path]] = None, device: str = "cpu"):
        self.device = device
        
        # Resolve model directory
        if model_dir is None:
            # Check model_assets subfolder first, then current folder
            curr = Path(__file__).resolve().parent
            if (curr / "model_assets").exists():
                self.model_dir = curr / "model_assets"
            else:
                self.model_dir = curr
        else:
            self.model_dir = Path(model_dir)

        self.model_path = self.model_dir / "Kurisu_e63_s37500.safetensors"
        self.config_path = self.model_dir / "config.json"
        self.style_vec_path = self.model_dir / "style_vectors.npy"

        if not self.model_path.exists():
            raise FileNotFoundError(f"Model checkpoint not found: {self.model_path}")
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        if not self.style_vec_path.exists():
            raise FileNotFoundError(f"Style vectors not found: {self.style_vec_path}")

        print(f"[*] Initializing Amadeus Voice Engine on {self.device.upper()}...")
        print(f"    Checkpoint: {self.model_path.name}")

        # 1. Load DeBERTa Japanese tokenizer & model
        bert_models.load_tokenizer(Languages.JP, "ku-nlp/deberta-v2-large-japanese-char-wwm")
        deberta = bert_models.load_model(Languages.JP, "ku-nlp/deberta-v2-large-japanese-char-wwm")
        deberta.to(torch.float32)

        # 2. Load TTS model
        self.tts = TTSModel(
            model_path=str(self.model_path),
            config_path=str(self.config_path),
            style_vec_path=str(self.style_vec_path),
            device=self.device,
        )
        self.tts.load()

        # 3. Load style matrix and calculate hypersphere manifold norm
        self.styles_matrix = np.load(self.style_vec_path)
        self.style2id = self.tts.style2id
        self.style_vectors = {
            emotion: self.styles_matrix[idx]
            for emotion, idx in self.style2id.items()
        }
        self.target_norm = float(np.mean([np.linalg.norm(v) for v in self.style_vectors.values()]))
        self.sampling_rate = self.tts.hyper_parameters.data.sampling_rate

        print(f"[+] Amadeus Voice Engine initialized successfully! (Target Norm: {self.target_norm:.4f})")

    def get_style_vector(self, emotion_blend: Dict[str, float]) -> np.ndarray:
        """
        Calculates composite style vector and projects it back onto the trained
        hypersphere manifold (L2 norm) to prevent vocoder comb-filtering / metallic sheen.
        """
        blended = np.zeros_like(next(iter(self.style_vectors.values())))
        for emotion, w in emotion_blend.items():
            if w > 0 and emotion in self.style_vectors:
                blended += w * self.style_vectors[emotion]

        current_norm = np.linalg.norm(blended)
        if current_norm > 0:
            blended = (blended / current_norm) * self.target_norm
        return blended

    def speak(
        self,
        text: str,
        emotions: Optional[Dict[str, float]] = None,
        length_scale: float = 1.0,
        noise_scale: float = 0.65,
        noise_scale_w: float = 0.80,
        sdp_ratio: float = 0.25,
        seed: Optional[int] = None,
    ) -> Tuple[int, np.ndarray]:
        """
        Synthesizes speech from Japanese text with unified optimal settings.
        
        Args:
            text: Japanese text to synthesize.
            emotions: Emotion weights dict, e.g. {"Warm": 0.65, "Neutral": 0.25, "Teasing": 0.10}.
                      Defaults to {"Neutral": 1.0}.
            length_scale: Speed scale (1.0 = normal, >1.0 slower, <1.0 faster).
            noise_scale: Stochastic generator noise (0.65 eliminates sibilant resonance).
            noise_scale_w: Syllable duration stochastic scale.
            sdp_ratio: Rhythm variation ratio.
            seed: Optional integer seed for deterministic output.
            
        Returns:
            Tuple of (sampling_rate, audio_numpy_array)
        """
        if emotions is None or len(emotions) == 0:
            emotions = {"Neutral": 1.0}

        style_vec = self.get_style_vector(emotions)

        if seed is not None:
            torch.manual_seed(seed)
            np.random.seed(seed)

        with torch.no_grad():
            audio = infer(
                text=text,
                sdp_ratio=sdp_ratio,
                noise_scale=noise_scale,
                noise_scale_w=noise_scale_w,
                length_scale=length_scale,
                sid=0,
                language=Languages.JP,
                hps=self.tts.hyper_parameters,
                net_g=self.tts._TTSModel__net_g,
                device=self.device,
                style_vec=style_vec,
            )
        return self.sampling_rate, audio

    def save_speech(
        self,
        text: str,
        output_path: Union[str, Path],
        emotions: Optional[Dict[str, float]] = None,
        length_scale: float = 1.0,
        seed: Optional[int] = None,
    ) -> Path:
        """
        Synthesizes speech and saves directly to a WAV file.
        """
        sr, audio = self.speak(text=text, emotions=emotions, length_scale=length_scale, seed=seed)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), audio, sr)
        return out
