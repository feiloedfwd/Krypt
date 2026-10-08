"""
KryptVM - serialiser / encryptor for compiled protos.

Pipeline (python side):
    protos -> bytes -> digit-shift numbers -> LZ77 -> 4 encryption layers
           -> junk-unicode encoding -> Lua string literal

The Luau side reverses it exactly (see runtime.py).
"""

import struct
import random

from .compiler import OP_SIG, OP_ID, OP_NAME, K


# --------------------------------------------------------------------------
# 32 bit xorshift PRNG (mirrored in Luau with bit32 - stays exact)
# --------------------------------------------------------------------------
class Rng:
    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF or 0x1234567

    def next32(self):
        s = self.s
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.s = s & 0xFFFFFFFF
        return self.s

    def byte(self):
        return self.next32() & 0xFF


# --------------------------------------------------------------------------
# bit writer
# --------------------------------------------------------------------------
class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.nb = 0

    def write(self, value, bits):
        self.acc |= (value & ((1 << bits) - 1)) << self.nb
        self.nb += bits
        while self.nb >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.nb -= 8

    def flush(self):
        while self.nb > 0:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.nb -= 8
        return bytes(self.out)


NUM_SHIFT = 3


def shift_number_text(txt):
    """d -> (d+NUM_SHIFT)%10, keeps - . e +"""
    out = []
    for c in txt:
        if '0' <= c <= '9':
            out.append(chr(ord('0') + (ord(c) - ord('0') + NUM_SHIFT) % 10))
        else:
            out.append(c)
    return ''.join(out)


def num_to_text(v):
    if v != v:
        return 'n'
    if v == float('inf'):
        return 'i'
    if v == float('-inf'):
        return '-i'
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(float(v))



# --------------------------------------------------------------------------
# wire format v2 helpers: per-constant encrypted cells + operand masking
# --------------------------------------------------------------------------
def expand_stream(seed32, n):
    r = Rng(seed32 or 0x1234567)
    return bytes(r.next32() & 0xFF for _ in range(n))


_DECOY_STRINGS = (
    'workspace', 'Players', 'Humanoid', 'GetService', 'Value', 'Speed', 'Position',
    'FireServer', 'InvokeServer', 'Character', 'AncestryChanged', 'Tick', 'Alpha',
    'result', 'cache', 'index', 'offset', 'Handler', 'Config', 'session', 'token',
    'CFrame', 'Magnitude', 'Transparency', 'Enabled', 'Parent', 'Name', 'Updater',
)


def decoy_const(rng):
    """Plausible-looking constant that no instruction references."""
    kind = rng.randrange(5)
    if kind == 0:
        return None
    if kind == 1:
        return bool(rng.randrange(2))
    if kind == 2:
        return float(rng.randint(-4096, 4096))
    if kind == 3:
        return rng.choice(_DECOY_STRINGS)
    return ''.join(rng.choice('abcdefghijklmnopqrstuvwxyz_') for _ in range(rng.randint(4, 14)))


def add_decoy_code(protos, rng, budget_ratio=0.30):
    """Append unreachable but well-formed instructions after each terminator.

    rng: random.Random (deterministic per seed; the loader never reproduces it).
    Capped at `budget_ratio` of the real instruction count: masked operands no
    longer compress, so padding every proto freely would inflate big scripts.
    """
    from .compiler import OP_SIG, OP_NAME
    budget = int(sum(len(p.code) for p in protos) * budget_ratio)
    for pr in protos:
        if not pr.code:
            continue
        if pr.code[-1][0] not in ('RETURN', 'HALT'):
            continue                      # only pad protos that cannot fall through
        if budget <= 0:
            break
        pad = min(budget, rng.randint(4, 16))
        budget -= pad
        for _ in range(pad):
            name = rng.choice([n for n in OP_NAME if n not in ('CLOSURE', 'HALT')])
            args = []
            for ch in OP_SIG[name]:
                if ch in ('w', 'c'):
                    args.append(rng.randint(1, 24))
                elif ch == 'n':
                    args.append(rng.randint(0, 255))
                elif ch == 'j':
                    args.append(rng.randint(-4, 4))
                elif ch == 'N':
                    args.append(rng.randint(1, max(1, len(protos))))
            pr.code.append((name,) + tuple(args))


def write_cell(bw, c, ci, pi, master):
    """One constant -> an encrypted cell (strings are split in two)."""
    def u8(v):
        bw.write(v & 0xFF, 8)

    def u16(v):
        bw.write(v & 0xFFFF, 16)

    if c is None:
        u8(0)
    elif c is False:
        u8(1)
    elif c is True:
        u8(2)
    elif isinstance(c, (int, float)):
        data = shift_number_text(num_to_text(float(c))).encode('utf-8')
        st = expand_stream(kdf(master, 512 + ci + 256 * (pi + 1), len(data)), len(data))
        u8(3)
        u16(len(data))
        for b, k in zip(data, st):
            bw.write(b ^ k, 8)
    else:
        data = c.encode('utf-8')
        n1 = len(data) // 2
        d1, d2 = data[:n1], data[n1:]
        st = expand_stream(kdf(master, 1024 + ci + 256 * (pi + 1),
                               (n1 << 16) | len(d2)), len(data))
        u8(4)
        u16(n1)
        for i, b in enumerate(d1):
            bw.write(b ^ st[i], 8)
        u16(len(d2))
        for i, b in enumerate(d2):
            bw.write(b ^ st[n1 + i], 8)


def proto_opcodes(master, pi, nslots=64):
    """File codeword (0..63) for each internal opcode id, per prototype."""
    r = Rng(kdf(master, pi, 0x0FF0) or 0x1234567)
    ids = list(range(nslots))
    for i in range(nslots - 1, 0, -1):
        j = r.next32() % (i + 1)
        ids[i], ids[j] = ids[j], ids[i]
    return ids          # ids[internal_id - 1] = file codeword


def encode_module(protos, rng, perm=None, master=None, proto_map=None, mask_mode=2):
    """Serialise all protos (wire format v2).

    master     : chain secret; per-proto/per-constant keys are derived from it,
                 so no key material for the constant pool appears in the file.
    proto_map  : optional list mapping "original proto index" -> "slot index"
                 (proto shuffle); CLOSURE operands are rewritten through it.
    """
    from .compiler import OP_SIG
    if master is None:
        master = 0x811C9DC5
    bw = BitWriter()

    def u8(v):
        bw.write(v & 0xFF, 8)

    def u16(v):
        bw.write(v & 0xFFFF, 16)

    u16(len(protos))
    for pi, pr in enumerate(protos):
        u8(pr.nparams)
        u8(1 if pr.vararg else 0)
        u8(len(pr.upvals))
        for cap in pr.upvals:
            u8(0 if cap['kind'] == 'reg' else 1)
            u8(cap['ref'])
        consts = list(pr.consts)
        crng = random.Random(kdf(master, 0x9000 + pi, len(consts)) or 0x1234567)
        for _ in range(max(1, len(consts) // 3)):       # decoy constants (never referenced)
            consts.append(decoy_const(crng))
        # shuffle the constant slots: file position != semantic index
        order = list(range(len(consts)))
        srng = Rng(kdf(master, 0x5150 + pi, len(consts)) or 0x1234567)
        for i in range(len(order) - 1, 0, -1):
            j = srng.next32() % (i + 1)
            order[i], order[j] = order[j], order[i]
        slot_of = [0] * len(order)
        for slot, idx in enumerate(order):
            slot_of[idx] = slot

        u16(len(pr.code))
        # operand masks (regenerated by the loader from the chain secret, never stored)
        #   mode 0: none                (smallest, most compressible)
        #   mode 1: one mask per proto  (repeated instructions still compress)
        #   mode 2: per-field stream    (strongest, incompressible)
        if mask_mode == 0:
            def m8():
                return 0

            def m16():
                return 0
        elif mask_mode == 1:
            mrng = Rng(kdf(master, pi, 0x5151) or 0x1234567)
            _k = mrng.next32()

            def m8():
                return _k & 0xFF

            def m16():
                return _k & 0xFFFF
        else:
            mrng = Rng(kdf(master, pi, len(pr.code)) or 0x1234567)

            def m8():
                return mrng.next32() & 0xFF

            def m16():
                return mrng.next32() & 0xFFFF

        xlat = proto_opcodes(master, pi)
        for ins in pr.code:
            name = ins[0]
            sig = OP_SIG[name]
            internal = (perm[name] if perm else OP_ID[name]) - 1
            bw.write(xlat[internal] & 0x3F, 6)
            for i, ch in enumerate(sig):
                v = ins[i + 1]
                if ch in ('w', 'n'):
                    bw.write((int(v) ^ m8()) & 0xFF, 8)
                elif ch in ('j', 'N'):
                    iv = int(v)
                    if ch == 'N' and proto_map is not None and name == 'CLOSURE':
                        iv = proto_map[iv]
                    bw.write((iv ^ m16()) & 0xFFFF, 16)
                elif ch == 'c':
                    if isinstance(v, K):
                        bw.write(1, 1)
                        bw.write((slot_of[v.i] ^ m16()) & 0xFFFF, 16)
                    else:
                        bw.write(0, 1)
                        bw.write((int(v) ^ m8()) & 0xFF, 8)
                else:
                    raise ValueError('bad sig char ' + ch)
        u16(len(consts))
        for slot in range(len(consts)):
            write_cell(bw, consts[order[slot]], slot, pi, master)
    return bw.flush()


# --------------------------------------------------------------------------
# LZ77 (LZSS) - decompressor lives in the Luau runtime
# --------------------------------------------------------------------------
WINDOW = 4096
MINMATCH = 3
MAXMATCH = 18


def lz_compress(data):
    """Greedy LZ77 with 3-byte hash chains (decoder mirrors this)."""
    n = len(data)
    out = bytearray()
    flags = 0
    flagbits = 0
    flagpos = 0
    head = {}
    prev = [0] * n

    def push_flag():
        nonlocal flags, flagbits, flagpos
        flagpos = len(out)
        out.append(0)
        flags = 0
        flagbits = 0

    push_flag()
    i = 0
    while i < n:
        best_len = 0
        best_off = 0
        if i + MINMATCH <= n:
            key = data[i:i + MINMATCH]
            j = head.get(key, -1)
            tries = 0
            limit = max(0, i - WINDOW)
            while j >= 0 and j >= limit and tries < 24:
                k = 0
                maxk = min(MAXMATCH, n - i)
                while k < maxk and data[j + k] == data[i + k]:
                    k += 1
                if k > best_len:
                    best_len = k
                    best_off = i - j
                    if k == MAXMATCH:
                        break
                j = prev[j]
                tries += 1
        if best_len >= MINMATCH:
            flags |= (1 << flagbits)
            out[flagpos] = flags
            off = best_off - 1
            out.append((off >> 8) & 0x0F)
            out.append(off & 0xFF)
            out.append((best_len - MINMATCH) & 0x0F)
            for _ in range(best_len):
                if i + MINMATCH <= n:
                    key = data[i:i + MINMATCH]
                    prev[i] = head.get(key, -1)
                    head[key] = i
                i += 1
        else:
            out.append(data[i])
            if i + MINMATCH <= n:
                key = data[i:i + MINMATCH]
                prev[i] = head.get(key, -1)
                head[key] = i
            i += 1
        flagbits += 1
        if flagbits == 8:
            push_flag()
    return bytes(out)


# --------------------------------------------------------------------------
# encryption layers (decryptor lives in the runtime, in reverse order)
# --------------------------------------------------------------------------
def _layer_xor(b, key):
    rng = Rng(key)
    out = bytearray(b)
    for i in range(len(out)):
        out[i] ^= rng.byte()
    return bytes(out)


def _layer_add(b, key):
    rng = Rng(key)
    out = bytearray(b)
    for i in range(len(out)):
        out[i] = (out[i] + rng.byte()) & 0xFF
    return bytes(out)


def _layer_rot(b, key):
    out = bytearray(b)
    for i in range(len(out)):
        r = ((i * 7 + key) % 8)
        v = out[i]
        out[i] = ((v << r) | (v >> (8 - r))) & 0xFF if r else v
    return bytes(out)


def _layer_nib(b):
    out = bytearray(b)
    for i in range(len(out)):
        v = out[i]
        out[i] = ((v >> 4) | ((v & 0x0F) << 4)) & 0xFF
    return bytes(out)


# --------------------------------------------------------------------------
# key-derivation chain (mirrored exactly in the loader)
#   mix32/rotl use only xor/shift/add so Python and Luau bit32 agree bit-for-bit
# --------------------------------------------------------------------------
M32 = 0xFFFFFFFF
_GOLDEN = 0x9E3779B9          # 2654435769


def _rotl(x, n):
    return ((x << n) | (x >> (32 - n))) & M32


def mix32(h, v):
    h = (h ^ v) & M32
    h = (h ^ _rotl(h, 7)) & M32
    h = (h + _GOLDEN) & M32
    h = (h ^ (h >> 11)) & M32
    h = (h ^ _rotl(h, 13)) & M32
    return h


def hash_bytes(data, seed=0x811C9DC5):
    h = seed & M32
    for b in data:
        h = mix32(h, b)
    return h


def chain_master(keys):
    """Master secret: never stored as a single literal in the loader."""
    return mix32(mix32(keys[0] ^ keys[3], keys[1]), keys[2])


def kdf(master, idx, prev):
    return mix32(mix32(master ^ ((idx + 1) & M32), prev), (idx + 0x5A5A) & M32)


def expand_key(seed32, n=16):
    r = Rng(seed32 or 0x1234567)
    return bytes(r.next32() & 0xFF for _ in range(n))


CELL_BASE = 384
CELL_SPAN = 256


def cell_size(master, i, axis):
    """Cell sizes are key-derived, so cell boundaries never appear in the file."""
    return CELL_BASE + (kdf(master, i, axis) % CELL_SPAN)


def chain_cells(data, master, axis):
    """Cell i is RC4'd with a key chained to cell i-1's *plaintext*.

    Sizes come from the secret, so a replay that fumbles one cell cannot
    resynchronise on the remainder of the stream.
    """
    out = bytearray()
    pos = 0
    prev = kdf(master, 0xFEED, axis)
    i = 0
    n_total = len(data)
    while True:
        n = cell_size(master, i, axis)
        cell = data[pos:pos + n]
        pos += n
        out += rc4(cell, expand_key(kdf(master, (i << 8) | (axis & 0xFF), prev)))
        prev = hash_bytes(cell, prev)
        i += 1
        if pos >= n_total:
            break
    return bytes(out), i


def unchain_cells(data, master, ncells, axis):
    out = bytearray()
    pos = 0
    prev = kdf(master, 0xFEED, axis)
    for i in range(ncells):
        n = cell_size(master, i, axis)
        cell = data[pos:pos + n]
        pos += n
        plain = rc4(cell, expand_key(kdf(master, (i << 8) | (axis & 0xFF), prev)))
        out += plain
        prev = hash_bytes(plain, prev)
    return bytes(out)


# --------------------------------------------------------------------------
# layer 5: bit-plane split  (even bytes then odd bytes)
# --------------------------------------------------------------------------
def plane_split(data):
    return data[0::2] + data[1::2]


def plane_merge(data, n_even):
    even, odd = data[:n_even], data[n_even:]
    out = bytearray(len(data))
    out[0:2 * len(even):2] = even
    out[1:2 * len(odd) + 1:2] = odd
    return bytes(out)


# --------------------------------------------------------------------------
# layer 6: block shuffle  (blocks permuted with the loader-side xorshift)
# --------------------------------------------------------------------------
BLOCK = 64


def nz(x):
    """Mirror the loader's `seed or 0x1234567` guard."""
    return (x & 0xFFFFFFFF) or 0x1234567


def shuffle_order(nblocks, seed):
    r = Rng(nz(seed))
    order = list(range(nblocks))
    for i in range(nblocks - 1, 0, -1):
        j = r.next32() % (i + 1)
        order[i], order[j] = order[j], order[i]
    return order


def block_shuffle(data, seed):
    pad = (-len(data)) % BLOCK
    d = data + b'\x00' * pad
    nb = len(d) // BLOCK
    order = shuffle_order(nb, seed)
    out = bytearray(len(d))
    for dst, src in enumerate(order):
        out[dst * BLOCK:(dst + 1) * BLOCK] = d[src * BLOCK:(src + 1) * BLOCK]
    return bytes(out), nb


# --------------------------------------------------------------------------
# layer 7: RC4 stream cipher (key derived from the seed at runtime)
# --------------------------------------------------------------------------
RC4_KEYLEN = 16


def derive_rc4_key(seed):
    r = Rng(nz(seed ^ 0xA5A5A5A5))
    return bytes(r.next32() & 0xFF for _ in range(RC4_KEYLEN))


def rc4(data, key):
    box = list(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + box[i] + key[i % klen]) & 0xFF
        box[i], box[j] = box[j], box[i]
    out = bytearray(len(data))
    a = b = 0
    for i, ch in enumerate(data):
        a = (a + 1) & 0xFF
        b = (b + box[a]) & 0xFF
        box[a], box[b] = box[b], box[a]
        out[i] = ch ^ box[(box[a] + box[b]) & 0xFF]
    return bytes(out)


def layer_stack(data, keys):
    d = data
    d = _layer_nib(d)
    d = _layer_rot(d, keys[2])
    d = _layer_add(d, keys[1])
    d = _layer_xor(d, keys[0])
    return d


def encrypt(data, keys):
    d = lz_compress(data)
    d = _layer_nib(d)
    d = _layer_rot(d, keys[2])
    d = _layer_add(d, keys[1])
    d = _layer_xor(d, keys[0])
    return d


# --------------------------------------------------------------------------
# junk-unicode encoding
#   one blob byte <-> one unicode char in U+2501..U+2600 (3 utf8 bytes)
# --------------------------------------------------------------------------
UNI_BASE = 0x2501


def junk_encode(data):
    return ''.join(chr(UNI_BASE + b) for b in data)


def make_keys(seed):
    r = random.Random(seed)
    return [r.getrandbits(32) for _ in range(4)]


def pack(protos, seed, perm=None, extra=None):
    """Returns (junk_text, meta) where meta holds key material for the runtime."""
    rng = Rng(seed ^ 0x5EED)
    keys = make_keys(seed)
    shuf_seed = nz(seed ^ 0x1234567)
    key_seed = nz(seed ^ 0xA5A5A5A5)
    master = chain_master(keys)
    extra = extra or {}
    add_decoy_code(protos, random.Random(seed ^ 0xDEC0DE))
    raw = encode_module(protos, rng, perm, master=master,
                        proto_map=extra.get('proto_map'),
                        mask_mode=extra.get('mask_mode', 2))
    comp = lz_compress(raw)                          # compress the bytecode first
    c1, n1 = chain_cells(comp, master, 0xA11CE)      # gate it cell-by-cell ...
    c2, n2 = chain_cells(c1, master, 0xBEEF)         # ... twice, with key-derived sizes
    enc = layer_stack(c2, keys)                      # nibble/rot/add/xor
    integ = hash_bytes(raw, 0xC0FFEE) ^ kdf(master, 0x7777, len(raw) & 0xFFFFFFFF)
    split = plane_split(enc)                     # layer 5: bit planes
    shuffled, nblocks = block_shuffle(split, shuf_seed)   # layer 6: block shuffle
    stream = rc4(shuffled, derive_rc4_key(seed))  # layer 7: RC4
    txt = junk_encode(stream)
    meta = {
        'keys': keys,
        'master': master,
        'mask_mode': extra.get('mask_mode', 2),
        'ncells1': n1,
        'ncells2': n2,
        'integ': integ,
        'rawl': len(raw),
        'raw_len': len(raw),
        'enc_len': len(enc),
        'n_even': (len(enc) + 1) // 2,
        'nblocks': nblocks,
        'shuf_seed': shuf_seed,
        'key_seed': key_seed,
        'padded_len': len(shuffled),
    }
    return txt, meta


def escape_unicode(txt):
    return ''.join('\\u{%04x}' % ord(c) for c in txt)
