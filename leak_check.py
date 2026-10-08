#!/usr/bin/env python3
"""Regression guard against the constant-pool leak.

Reproduces the strongest *static* attack: peel every documented layer, break the
chained cells (given the master), and scan the decoded bytecode for anything
readable. The obfuscated file must not expose the program's strings, numbers or
identifiers at rest.

    python3 tests/leak_check.py tests/t1.lua tests/t5.lua
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from obf.frontend import parse                      # noqa: E402
from obf.compiler import compile_ast                # noqa: E402
from obf import packer as P                         # noqa: E402
from obf.runtime import RuntimeGen, mix_protos      # noqa: E402

SEED = 4242


def unlz(s):
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        flags = s[i]
        i += 1
        for _ in range(8):
            if i > n - 1:
                break
            if flags & 1:
                b1, b2, b3 = s[i], s[i + 1], s[i + 2]
                i += 3
                d = b1 * 256 + b2 + 1
                ln = b3 + 3
                st = len(out) - d
                for k in range(ln):
                    out.append(out[st + k])
            else:
                out.append(s[i])
                i += 1
            flags >>= 1
    return bytes(out)


def unxor(b, key):
    r = P.Rng(key)
    o = bytearray(b)
    for i in range(len(o)):
        o[i] ^= r.byte()
    return bytes(o)


def unadd(b, key):
    r = P.Rng(key)
    o = bytearray(b)
    for i in range(len(o)):
        o[i] = (o[i] - r.byte()) % 256
    return bytes(o)


def attack(txt, meta):
    """Every layer an analyst can replay from the loader, plus the chain."""
    blob = bytes((ord(c) - P.UNI_BASE) % 256 for c in txt)
    blob = P.rc4(blob, P.derive_rc4_key(meta['seed_for_outer']))
    nb = len(blob) // 64
    rng = P.Rng(meta['shuf_seed'])
    order = list(range(nb))
    for i in range(nb - 1, 0, -1):
        j = rng.next32() % (i + 1)
        order[i], order[j] = order[j], order[i]
    parts = [None] * nb
    for dst in range(nb):
        parts[order[dst]] = blob[dst * 64:(dst + 1) * 64]
    blob = b''.join(parts)[:meta['enc_len']]
    ev, od = 0, meta['n_even']
    out = bytearray()
    for i in range(len(blob)):
        if i % 2 == 0:
            out.append(blob[ev]); ev += 1
        else:
            out.append(blob[od]); od += 1
    blob = bytes(out)
    k = meta['keys']
    blob = unxor(blob, k[0])
    blob = unadd(blob, k[1])
    o = bytearray(blob)
    for i in range(len(o)):
        sh = (i * 7 + k[2]) % 8
        if sh:
            v = o[i]
            o[i] = ((v >> sh) | (v << (8 - sh))) & 0xFF
    blob = P._layer_nib(bytes(o))
    blob = P.unchain_cells(blob, meta['master'], meta['ncells2'], 0xBEEF)
    blob = P.unchain_cells(blob, meta['master'], meta['ncells1'], 0xA11CE)
    return unlz(blob)


def literals_of(src):
    """Strings/identifiers an attacker would look for."""
    found = set(re.findall(r'"([^"\n]{4,})"', src))
    found |= set(re.findall(r"'([^'\n]{4,})'", src))
    found |= set(re.findall(r'\b([a-z_][a-z_0-9]{5,})\b', src))
    return {f for f in found if len(f) >= 5}


def main(paths):
    ok_all = True
    for path in paths:
        src = open(path).read()
        protos = compile_ast(parse(src))
        gen = RuntimeGen(protos, SEED, False)
        shuffled, inv = mix_protos(protos, SEED)
        txt, meta = P.pack(shuffled, SEED, gen.perm,
                           extra={'proto_map': inv, 'entry': inv[0] + 1,
                                  'mask_mode': 2})
        meta['seed_for_outer'] = gen.seed
        raw = attack(txt, meta)
        text = raw.decode('latin1')
        leaks = sorted(lit for lit in literals_of(src) if lit in text)
        printable = re.findall(r'[ -~]{6,}', text)
        print('%-16s decoded=%6d B  printable-runs=%3d  leaked-literals=%d'
              % (os.path.basename(path), len(raw), len(printable), len(leaks)))
        if leaks:
            print('   LEAK:', leaks[:8])
            ok_all = False
        else:
            print('   no program literals recoverable statically')
    print('NO LEAKS' if ok_all else 'LEAKS FOUND')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or ['tests/t1.lua', 'tests/t5.lua']))
