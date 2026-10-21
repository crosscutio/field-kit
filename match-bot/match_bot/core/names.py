"""Number-aware place-name parsing.

A name splits into a *base* (the words) and a *number set*. Fuzzy scoring
looks only at the base, so a shared trailing "2" can't make "Ward 2" look
like "Zone 2"; the number sets are a hard gate, so "Ofutop 1" never pairs
with "Ofutop 2" and a numbered name never pairs with an unnumbered one.

Sources write the same number many ways, and all of these parse to 2:
    "Ofutop 2", "OFUTOP_2", "Ofutop2", "Ofutop II", "Ikot Eyo Two (2)", "Ebedeng 11"
"""

import re
from typing import FrozenSet, NamedTuple

from .standardization import remove_accents

_ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5,
          'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}
_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
          'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
# Roman numerals typed with digits ("Ebedeng 11", "Patigi 1v"); both readings kept.
_DIGIT_ROMAN = {'11': 2, '111': 3}

_ORDINAL = re.compile(r'\b(\d+)(?:st|nd|rd|th)\b')
_ONE_V = re.compile(r'\b1v\b')
_GLUED = re.compile(r'(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])')
_NON_ALNUM = re.compile(r'[^a-z0-9]+')


class ParsedName(NamedTuple):
    base: str
    numbers: FrozenSet[int]   # every accepted reading; used for compatibility
    literal: tuple            # numbers as written, in order; used for canonical()


def tokens(s) -> list:
    if s is None or (isinstance(s, float) and s != s):
        return []
    s = remove_accents(str(s)).lower().replace("'", '').replace('’', '')
    s = _ORDINAL.sub(r'\1', s)
    s = _ONE_V.sub('iv', s)
    s = _NON_ALNUM.sub(' ', s)
    s = _GLUED.sub(' ', s)
    return s.split()


def _value(toks: list, pos: int, trailing: bool):
    """Number a token stands for, or None. Never the first token. Roman
    numerals count anywhere after it ("Amana I Main Town", "Okoroba Ii
    Mbianang"), except a one-letter numeral after another initial ("J I
    Barrack", "F I Giwa"). Number words only count in the trailing run, so
    "Four Corner" and "One Man Country" keep their words."""
    tok = toks[pos]
    if tok.isdigit():
        return int(tok)
    if pos == 0:
        return None
    if tok in _ROMAN and (len(tok) > 1 or trailing or len(toks[pos - 1]) > 1):
        return _ROMAN[tok]
    return _WORDS.get(tok) if trailing else None


def parse_name(s) -> ParsedName:
    toks = tokens(s)
    if not toks:
        return ParsedName('', frozenset(), ())

    # Trailing run of numbers (and "and" joining them, as in "1 and 2").
    start = len(toks)
    while start > 0:
        t = toks[start - 1]
        if _value(toks, start - 1, True) is not None:
            start -= 1
        elif t == 'and' and start < len(toks) and start - 2 >= 0 \
                and _value(toks, start - 2, True) is not None:
            start -= 1
        else:
            break

    words, literal, numbers = [], [], set()
    for i, t in enumerate(toks):
        v = _value(toks, i, i >= start)
        if v is None:
            if not (t == 'and' and i >= start):
                words.append(t)
            continue
        literal.append(v)
        numbers.add(v)
        if t in _DIGIT_ROMAN:
            numbers.add(_DIGIT_ROMAN[t])

    base = ' '.join(words) if words else ' '.join(toks)
    return ParsedName(base, frozenset(numbers), tuple(literal))


def numbers_compatible(a, b) -> bool:
    """True when neither name is numbered, or both are and share a number.
    Accepts raw strings or ParsedName."""
    na = (a if isinstance(a, ParsedName) else parse_name(a)).numbers
    nb = (b if isinstance(b, ParsedName) else parse_name(b)).numbers
    if not na and not nb:
        return True
    return bool(na & nb)


def canonical(s) -> str:
    """Base plus its numbers as digits: "OFUTOP_2", "Ofutop II", "ofutop2" -> "ofutop 2".
    Unnumbered names are only lowercased; their punctuation is left alone."""
    p = parse_name(s)
    if not p.literal:
        return ' '.join(str(s).lower().split()) if s is not None and s == s else ''
    if p.base == ' '.join(tokens(s)):
        return p.base
    return ' '.join([p.base] + [str(n) for n in dict.fromkeys(p.literal)])
