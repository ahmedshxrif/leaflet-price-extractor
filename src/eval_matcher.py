"""
Synthetic-noise evaluation for the Stage 2 matcher — runs WITHOUT any leaflets.

We take real master codes, corrupt them the way a scanner would, and check the
matcher's behaviour. The cardinal sin is a WRONG confident match (matched to a
different code). Correct-or-flagged is acceptable; wrong-and-confident is not.

Three noise regimes:
  CLEAN      — no corruption. Must exact-match.
  CONFUSION  — swap 1-2 chars within an OCR confusion class (0<->O, 5<->S ...).
               Should recover to the SAME code (exact/confusion), or flag.
  HEAVY      — random substitutions/drops. Should recover or flag — never wrong.

Usage:  python src/eval_matcher.py
"""

import random

from matcher import Matcher, _CONFUSION_CLASSES

SEED = 20260715
N_PER_REGIME = 400

# char -> a different class-mate, for generating realistic confusion noise
_SWAP = {}
for cls in _CONFUSION_CLASSES:
    for c in cls:
        _SWAP[c] = [d for d in cls if d != c]


def confusion_noise(code: str, rng: random.Random, k: int = 2) -> str:
    pos = [i for i, c in enumerate(code) if c in _SWAP]
    rng.shuffle(pos)
    out = list(code)
    for i in pos[:k]:
        out[i] = rng.choice(_SWAP[out[i]])
    return "".join(out)


def heavy_noise(code: str, rng: random.Random) -> str:
    out = list(code)
    for _ in range(rng.randint(1, 2)):
        i = rng.randrange(len(out))
        if rng.random() < 0.5 and len(out) > 4:
            out.pop(i)                                   # dropped char
        else:
            out[i] = rng.choice("ABCDEFGHJKMNPRTUVWXY0123456789")  # wrong char
    return "".join(out)


def run():
    m = Matcher()
    rng = random.Random(SEED)
    # sample from LONG codes (short codes are exact-only by policy, tested separately)
    long_codes = [s for s in m.all_stems if not m._is_short.get(s, False)]
    sample = rng.sample(long_codes, min(N_PER_REGIME, len(long_codes)))

    for regime, noiser in [
        ("CLEAN", lambda c, r: c),
        ("CONFUSION", confusion_noise),
        ("HEAVY", heavy_noise),
    ]:
        # a token whose normalized form IS another real code can't be blamed on
        # the matcher — the noise destroyed the original's identity.
        real_stems = set(m.all_stems)
        correct = flagged = collision = misjudged = 0
        bad_examples = []
        for code in sample:
            noisy = noiser(code, rng)
            r = m.match(noisy)
            if r.matched_code is None:
                flagged += 1
            elif r.matched_code == code:
                correct += 1
            elif r.norm_token in real_stems:
                collision += 1                          # became a different real code
            else:
                misjudged += 1                          # genuine matcher error
                if len(bad_examples) < 5:
                    bad_examples.append((code, noisy, r.matched_code, r.method))
        n = len(sample)
        print(f"\n{regime:10} n={n}")
        print(f"  correct   : {correct:4}  ({correct/n:5.1%})")
        print(f"  flagged   : {flagged:4}  ({flagged/n:5.1%})  <- safe: review, not wrong")
        print(f"  collision : {collision:4}  ({collision/n:5.1%})  <- noise made it another REAL code (unavoidable)")
        print(f"  MISJUDGED : {misjudged:4}  ({misjudged/n:5.1%})  <- genuine matcher error, must be ~0")
        for orig, noisy, got, meth in bad_examples:
            print(f"      {orig} --noise--> {noisy}  MISJUDGED as {got} ({meth})")

    # short-code policy check: a near-miss on a short code must NOT fuzzy-match
    print("\nSHORT-CODE policy spot-check:")
    shorts = [s for s in m.all_stems if m._is_short.get(s, False)][:5]
    for s in shorts:
        noisy = confusion_noise(s, rng, k=1)
        r = m.match(noisy)
        verdict = "flagged (correct)" if r.matched_code is None else f"matched {r.matched_code}"
        print(f"  {s} --> {noisy}: {verdict}")


if __name__ == "__main__":
    run()
