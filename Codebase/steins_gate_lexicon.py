"""
steins_gate_lexicon.py

Backend-agnostic proper-noun corrector for the Amadeus voice pipeline.
Works on raw text output from SenseVoice, Whisper, or any transducer model.

Fixes the class of error you're seeing: ASR models have never seen
"Hououin Kyouma" in training data, so they output the nearest thing their
vocabulary *does* know. Rather than chasing a "smarter" model, this matches
transcript fragments against a canon glossary and swaps in the correct term.

Install: pip install rapidfuzz
"""

from rapidfuzz import fuzz, process

# Canon term -> known near-miss variants you've actually observed.
# Keep growing this list as you (or GitHub users) hit new failure cases —
# it's cheap to extend and doesn't require touching the model at all.
CANON_TERMS = {
    "Hououin Kyouma": ["hoin kyoma", "howin kyoma", "hoyo in kyoma", "hoin quoma"],
    "Makise Kurisu": ["mah kese kurisu", "mikasi kurisu", "mccusey kurisu", "makise curry sue"],
    "Amadeus": ["i'm adeus", "im adeus", "amma day us", "ahmadeus", "a maideus"],
    "El Psy Kongroo": ["el psy congro", "el pussy kangaroo", "el say kongru", "el p si kongru"],
    "Divergence Meter": ["divergence meeter", "diver gents meter", "divergent's meter"],
    "Steins;Gate": ["stein's gate", "steins gate", "stains gate"],
    "IBN 5100": ["i b n 5100", "iben 5100", "iban 5100"],
    "Time Leap": ["time leep", "time lead"],
    "Worldline": ["world line", "world lion"],
    "SERN": ["surn", "circe"],  # NOTE: real "CERN" will also collide here — remove
                                  # or add context-gating if that's a problem for you.
}


def build_hotwords_file(path: str, score: float = 2.5) -> None:
    """
    Write a sherpa-onnx hotwords.txt from the canon list, so the glossary
    stays in one place instead of being duplicated between the corrector
    and the decoder config. Only used if you add a transducer model later.
    """
    with open(path, "w", encoding="utf-8") as f:
        for term in CANON_TERMS:
            f.write(f"{term.upper()} :{score}\n")


def correct_transcript(text: str, threshold: float = 72.0) -> str:
    """
    Fuzzy-correct a transcript against the canon glossary.

    Key design choice: each canon term is only compared against windows
    close to its OWN word count (+/-1, to allow for ASR over/under-splitting
    like "I'm Adeus" for "Amadeus"), and scored with a full-sequence ratio
    rather than a partial/substring ratio. This is what keeps short common
    words ("can", "hear", "you") from spuriously matching long canon
    phrases — a broad "any window vs any term" search will false-positive
    constantly on a small glossary. Terms are tried longest-first so e.g.
    "Hououin Kyouma" is claimed before a shorter/looser term could grab
    part of it.

    `threshold` is a 0-100 ratio score. 72 was tuned against the sample
    errors in this file's __main__ block — raise it if you see false
    positives on your own recordings, lower it if real near-misses slip
    through.
    """
    words = text.split()
    used = set()
    # canon terms are compared longest-first so multi-word names get first
    # claim on overlapping spans
    terms_by_len = sorted(CANON_TERMS, key=lambda t: -len(t.split()))

    matches = []  # (start_idx, end_idx_exclusive, canon_term)
    for term in terms_by_len:
        term_len = len(term.split())
        candidates = []  # (score, start_idx, window)
        for window in {max(1, term_len - 1), term_len, term_len + 1}:
            for i in range(len(words) - window + 1):
                span = range(i, i + window)
                if any(idx in used for idx in span):
                    continue
                chunk = " ".join(words[i:i + window])
                score = fuzz.ratio(chunk.lower(), term.lower())
                candidates.append((score, i, window))
        if candidates:
            best_score, i, window = max(candidates, key=lambda c: c[0])
            if best_score >= threshold:
                matches.append((i, i + window, term))
                used.update(range(i, i + window))

    # rebuild the sentence, splicing in canon terms at their matched spans
    matches.sort(key=lambda m: m[0])
    out, cursor = [], 0
    for start, end, term in matches:
        out.extend(words[cursor:start])
        out.append(term)
        cursor = end
    out.extend(words[cursor:])
    return " ".join(out)


if __name__ == "__main__":
    tests = [
        "I'm Adeus can you hear me",
        "hoin kyoma is my chuunibyou alter ego",
        "mah kese kurisu doesn't like that nickname",
        "we need to find the iban 5100 before the time leep window closes",
    ]
    for t in tests:
        print(f"{t!r}\n  -> {correct_transcript(t)!r}\n")
