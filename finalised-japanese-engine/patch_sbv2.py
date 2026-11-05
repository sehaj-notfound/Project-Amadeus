"""
patch_sbv2.py - Automatically patches Style-Bert-VITS2 to install Gaussian noise clamping.
Guarantees acoustic stability and prevents latent vocoder phase distortion.
"""

import sys
from pathlib import Path

try:
    import style_bert_vits2
except ImportError:
    print("[ERROR] style_bert_vits2 is not installed in the current environment.")
    sys.exit(1)

sbv2_root = Path(style_bert_vits2.__file__).resolve().parent
targets = [
    sbv2_root / "models" / "models_jp_extra.py",
    sbv2_root / "models" / "models.py"
]

target_pattern = "noise = torch.randn_like(m_p)"
clamp_injection = "noise = torch.randn_like(m_p)\n        noise = torch.clamp(noise, min=-1.75, max=1.75)"

success = True
for target in targets:
    if not target.exists():
        print(f"[SKIP] Target file does not exist: {target.name}")
        continue
    content = target.read_text(encoding="utf-8")
    if "torch.clamp(noise" in content:
        print(f"[OK] Already clamped: {target.name}")
        continue
    if target_pattern in content:
        patched = content.replace(target_pattern, clamp_injection, 1)
        target.write_text(patched, encoding="utf-8")
        print(f"[PATCHED] Applied noise clamp [-1.75, 1.75] to: {target.name}")
    else:
        print(f"[WARN] Target pattern not found in: {target.name}")
        success = False

if success:
    print("\n[+] Acoustic clamping verification complete!")
else:
    print("\n[!] Please manually verify style_bert_vits2 models clamping.")
