"""Minifier for generated Lua/Luau.

The loader is machine-generated, so a conservative token-level join is safe:
string/number/name boundaries are preserved and any newline-sensitive
ambiguity (a statement starting with `(` right after an expression) would
already be a syntax error in the formatted source, so joining tokens never
changes how the chunk parses.

Usage:
    from obf.minify import minify
    dense = minify(source)
"""
import re

_WS = ' \t\r\n\v\f'
_NAME0 = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_')
_NAME = _NAME0 | set('0123456789')
_NUMCH = set('0123456789abcdefABCDEFxX.eEpP+-')
_OPS = (
    '...', '..', '==', '~=', '<=', '>=', '//', '<<', '>>', '::',
    '+', '-', '*', '/', '%', '^', '#', '&', '|', '~', '<', '>', '=',
    '(', ')', '{', '}', '[', ']', ';', ':', ',', '.',
)


def _long_bracket(src, i):
    """If src[i] opens a long bracket ([[ / [=[ ...), return (level, body_start)."""
    if src[i] != '[':
        return None
    j = i + 1
    level = 0
    while j < len(src) and src[j] == '=':
        level += 1
        j += 1
    if j < len(src) and src[j] == '[':
        return level, j + 1
    return None


def tokenize(src):
    """Return [(kind, text)] with kinds: name, num, str, op."""
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in _WS:
            i += 1
            continue
        # comments
        if c == '-' and i + 1 < n and src[i + 1] == '-':
            lb = _long_bracket(src, i + 2)
            if lb:
                level, j = lb
                close = ']' + '=' * level + ']'
                k = src.find(close, j)
                i = n if k < 0 else k + len(close)
            else:
                k = src.find('\n', i)
                i = n if k < 0 else k + 1
            continue
        # long strings
        lb = _long_bracket(src, i)
        if lb is not None:
            level, j = lb
            close = ']' + '=' * level + ']'
            k = src.find(close, j)
            end = n if k < 0 else k + len(close)
            toks.append(('str', src[i:end]))
            i = end
            continue
        # quoted strings
        if c in '"\'':
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == c:
                    j += 1
                    break
                j += 1
            toks.append(('str', src[i:j]))
            i = j
            continue
        # backtick interpolated strings (Luau):  `a {expr} b`
        if c == '`':
            j, depth = i + 1, 0
            while j < n:
                ch = src[j]
                if ch == '\\':
                    j += 2
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                elif ch == '`' and depth <= 0:
                    j += 1
                    break
                elif depth > 0 and ch in '"\'':
                    k = j + 1
                    while k < n:
                        if src[k] == '\\':
                            k += 2
                            continue
                        if src[k] == ch:
                            break
                        k += 1
                    j = k + 1
                    continue
                j += 1
            toks.append(('str', src[i:j]))
            i = j
            continue
        # names / keywords
        if c in _NAME0:
            j = i + 1
            while j < n and src[j] in _NAME:
                j += 1
            toks.append(('name', src[i:j]))
            i = j
            continue
        # numbers
        if c.isdigit() or (c == '.' and i + 1 < n and src[i + 1].isdigit()):
            j = i
            if src.startswith(('0x', '0X'), i):
                j = i + 2
                while j < n and src[j] in _NUMCH:
                    j += 1
            else:
                while j < n and (src[j].isdigit() or src[j] == '.'):
                    j += 1
                if j < n and src[j] in 'eE':
                    j += 1
                    if j < n and src[j] in '+-':
                        j += 1
                    while j < n and src[j].isdigit():
                        j += 1
            toks.append(('num', src[i:j]))
            i = j
            continue
        # operators
        for op in _OPS:
            if src.startswith(op, i):
                toks.append(('op', op))
                i += len(op)
                break
        else:
            toks.append(('op', c))
            i += 1
    return toks


def _need_space(prev, cur):
    pk, pt = prev
    ck, ct = cur
    # word / number adjacency
    if pk in ('name', 'num') and ck in ('name', 'num'):
        return True
    if pk in ('str', 'num') and ck == 'name':
        return True
    # operator collisions that would change tokenisation
    if pt.endswith('-') and ct.startswith('-'):
        return True
    if pt.endswith('[') and ct.startswith(('[', '=')):
        return True
    if pk == 'num' and ct.startswith('.'):
        return True
    if pt.endswith('.') and ct.startswith('.'):
        return True
    return False


def minify(src):
    """Strip comments/whitespace and join tokens with the minimum separators."""
    out = []
    prev = None
    for kind, text in tokenize(src):
        if prev is not None and _need_space(prev, (kind, text)):
            out.append(' ')
        out.append(text)
        prev = (kind, text)
    return ''.join(out)


def strip_comments(src):
    """Whitespace-preserving comment stripper (useful for debugging)."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '-' and i + 1 < n and src[i + 1] == '-':
            lb = _long_bracket(src, i + 2)
            if lb:
                level, j = lb
                close = ']' + '=' * level + ']'
                k = src.find(close, j)
                i = n if k < 0 else k + len(close)
            else:
                k = src.find('\n', i)
                i = n if k < 0 else k + 1
            continue
        if c in '"\'':
            j = i + 1
            while j < n:
                if src[j] == '\\':
                    j += 2
                    continue
                if src[j] == c:
                    j += 1
                    break
                j += 1
            out.append(src[i:j])
            i = j
            continue
        out.append(c)
        i += 1
    return ''.join(out)
