"""
Name audition: plays different SPELLINGS of a name, inside a sentence, through the real
Kurisu voice, so you can pick the one that sounds right by ear.

    python voice_try.py                          built-in candidates for "Chondria"
    python voice_try.py Kondreea "Con dree ah"   your own spellings
    python voice_try.py --name Foo --sentence "Welcome to the {} lab." Fooh Fu

Why spellings matter: the voice only hears PHONEMES. A respelling helps only if it changes
the phonemes; "Condria", "Kahndria" and "Kohndria" all collapse to the same sounds as
"Kondria", so they'd sound identical. The built-in list holds spellings that really differ.
Avoid hyphens (they add pauses) and a bare "-ah"/"-uh" tail (can turn into "hah"/"oo").

Run from the same folder / venv as chat_terminal.py. The voice service is started if needed.
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

import sounddevice as sd

import tts_engine as te

BUILTIN = [
    "Kondria",       # current setting (the one that came out as "Kayondria")
    "Kondreea",      # stress moved onto "dree": kon-DREE-uh
    "Con dree ah",   # three separate words; ends on a full "ah" instead of a schwa
    "Candria",       # different first vowel ("can-")
    "Kondreeah",     # reduced first vowel: kuhn-DREE-uh
]


def play(backend, sentence: str, cand: str, key: str, tag: str):
    text = sentence.replace("{}", cand)
    spoken, _ = te.TTSSanitizer.sanitize(text)
    print(f"\n{tag}  spelling: {cand!r}\n{' ' * len(tag)}  sent    : {spoken}")
    audio, sr = backend.synthesize(spoken, key)
    sd.play(audio, sr)
    sd.wait()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidates", nargs="*", help="spellings to try (default: built-in list)")
    ap.add_argument("--name", default="Chondria", help="the written form these respell")
    ap.add_argument("--sentence", default="I'm from Viktor {} University.", help="{} is replaced by each spelling")
    ap.add_argument("--key", default=te.DEFAULT_VOICE_KEY, help="voice tag key to speak with")
    args = ap.parse_args()
    cands = args.candidates or BUILTIN
    if "{}" not in args.sentence:
        ap.error("--sentence must contain {} where the name goes")

    backend = te.detect_tts_backend()
    if not isinstance(backend, te.GPTSoVITSBackend):
        print("GPT-SoVITS isn't available, so there is nothing to audition. Exiting.")
        return
    backend.initialize()  # includes the warm-up

    print(f'\nSentence: "{args.sentence}"   (voice: {args.key})')
    for i, c in enumerate(cands, 1):
        play(backend, args.sentence, c, args.key, f"[{i}]")

    while True:
        pick = input(f"\nReplay which one? (1-{len(cands)}, or Enter to finish): ").strip()
        if not pick:
            break
        if pick.isdigit() and 1 <= int(pick) <= len(cands):
            play(backend, args.sentence, cands[int(pick) - 1], args.key, f"[{pick}]")

    best = input(f"Which sounded best? (1-{len(cands)}, Enter to skip): ").strip()
    if best.isdigit() and 1 <= int(best) <= len(cands):
        print("\nIn tts_engine.py, set the PRONUNCIATIONS table to:\n")
        print("PRONUNCIATIONS = {")
        print(f'    "{args.name}": "{cands[int(best) - 1]}",')
        print("}\n")
    else:
        print("\nNone worked? Tell me what each one sounded like and I'll pick new spellings.")


if __name__ == "__main__":
    main()
