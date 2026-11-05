"""
Stutter audition script. Two modes, both play through the REAL Kurisu voice:

    python stutter_test.py            play the COMMON stutters (wh, I, n, b, s, t, d, y, h, th, sh, w)
                                      with your CURRENT settings in tts_engine.py; flag any that sound off
    python stutter_test.py ab         A/B: compare ways of writing "Wh-what" (already decided: "Wah-what")

Run from the same folder / venv as chat_terminal.py. The voice service is started
automatically if it isn't running.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import sounddevice as sd

import tts_engine as te

DEFAULTS = (te.STUTTER_MODE, te.STUTTER_VOWEL, te.STUTTER_JOIN)   # whatever tts_engine.py says now

# ── A/B mode ──────────────────────────────────────────────────────────────────
AB_LINE = "Wh-what kind of ridiculous question is that?!"
AB_KEY = "tsundere_fast_nervous"
VARIANTS = [
    ("1", "phonetic, hyphen      (Wuh-what)",   dict(STUTTER_MODE="phonetic", STUTTER_VOWEL="uh", STUTTER_JOIN="-")),
    ("2", "phonetic, ellipsis    (Wuh... what)", dict(STUTTER_MODE="phonetic", STUTTER_VOWEL="uh", STUTTER_JOIN="... ")),
    ("3", "phonetic, comma       (Wuh, what)",  dict(STUTTER_MODE="phonetic", STUTTER_VOWEL="uh", STUTTER_JOIN=", ")),
    ("4", "phonetic 'ah', hyphen (Wah-what)",   dict(STUTTER_MODE="phonetic", STUTTER_VOWEL="ah", STUTTER_JOIN="-")),
    ("5", "repeat the word       (What, what)", dict(STUTTER_MODE="repeat")),
]

# ── common-stutters mode ──────────────────────────────────────────────────────
COMMON = [
    ("tsundere_fast_nervous", "Wh-what kind of ridiculous question is that?!"),
    ("tsundere_fast_nervous", "I-I didn't mean it like that!"),
    ("tsundere_fast_nervous", "N-not that I care or anything!"),
    ("angry_fast_loud",       "B-baka! S-shut up!"),
    ("tsundere_slow_relaxed", "T-that's not what I meant."),
    ("tsundere_fast_nervous", "D-don't look at me like that!"),
    ("happy_fast_nervous",    "Y-you really think so?"),
    ("tsundere_fast_nervous", "H-huh?! Th-that's ridiculous!"),
    ("angry_fast_loud",       "Sh-shut up already!"),
    ("tsundere_fast_nervous", "W-wait, that's not fair!"),
]


def apply(settings: dict):
    te.STUTTER_MODE, te.STUTTER_VOWEL, te.STUTTER_JOIN = DEFAULTS
    for k, v in settings.items():
        setattr(te, k, v)


def say(backend, line: str, key: str, tag: str = ""):
    spoken, _ = te.TTSSanitizer.sanitize(line)
    print(f"\n{tag}  written: {line}\n{' ' * len(tag)}  spoken : {spoken}")
    audio, sr = backend.synthesize(spoken, key)
    sd.play(audio, sr)
    sd.wait()


def ab_mode(backend):
    print(f'\nOriginal line: "{AB_LINE}"   (voice: {AB_KEY})')
    for num, label, settings in VARIANTS:
        apply(settings)
        say(backend, AB_LINE, AB_KEY, f"[{num}] {label}")

    table = {v[0]: v for v in VARIANTS}
    while True:
        pick = input("\nReplay which one? (1-5, or Enter to finish): ").strip()
        if not pick:
            break
        if pick in table:
            apply(table[pick][2])
            say(backend, AB_LINE, AB_KEY, f"[{pick}] {table[pick][1]}")

    best = input("Which one sounded best? (1-5, Enter to skip): ").strip()
    if best in table:
        print("\nPut these lines in tts_engine.py (search for STUTTER_MODE):\n")
        for k, v in table[best][2].items():
            print(f"    {k} = {v!r}")
        print()


def common_mode(backend):
    apply({})
    print(f"\nSettings in use: mode={te.STUTTER_MODE!r} vowel={te.STUTTER_VOWEL!r} join={te.STUTTER_JOIN!r}")
    for i, (key, line) in enumerate(COMMON, 1):
        say(backend, line, key, f"[{i:2d}]")

    bad = input("\nAny that sounded wrong? Numbers separated by spaces, or Enter if all good: ").split()
    frags = []
    for n in bad:
        if n.isdigit() and 1 <= int(n) <= len(COMMON):
            for m in te._STUTTER_RE.finditer(COMMON[int(n) - 1][1]):
                for f in m.group(1).split("-"):
                    if f and f.lower() not in frags:
                        frags.append(f.lower())
    if frags:
        print("\nAdd exceptions to STUTTER_SOUNDS in tts_engine.py. The value is how it should")
        print("sound, spelled so the voice can read it. Examples to try, per fragment:\n")
        for f in frags:
            print(f'    "{f}": "{f}uh",     # or "{f}oh", "{f}eh", ...')
        print("\n(or just tell me which numbers were off and what they sounded like)")
    elif bad == []:
        print("\nAll good. Nothing to change.")


def main():
    backend = te.detect_tts_backend()
    if not isinstance(backend, te.GPTSoVITSBackend):
        print("GPT-SoVITS isn't available, so there is nothing to audition. Exiting.")
        return
    backend.initialize()  # includes the warm-up
    print(f"Engine file in use: {te.__file__}")   # confirms you are not running a stale copy
    (ab_mode if "ab" in sys.argv[1:] else common_mode)(backend)


if __name__ == "__main__":
    main()
