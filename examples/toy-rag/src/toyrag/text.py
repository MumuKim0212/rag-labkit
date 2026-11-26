"""Words, sentences and a rough token count: the little text handling the toy system and the toy judge share."""

import re

STOPWORDS = frozenset("""
a an and are as at be been by can do does for from get go has have how i if in is it its long many may much
must my of on or per should so than that the their there they this to up was we what when which who why will with
you your
""".split())


def words(text: str) -> list[str]:
    """Lower-case words without stopwords, plural `s` removed (laptops -> laptop, but not `class`)."""
    out = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def tokens(text: str) -> int:
    """About four characters to a token, the usual rule of thumb. The toy models are simulated, so are their tokens."""
    return max(1, len(text) // 4)
