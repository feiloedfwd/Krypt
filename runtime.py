"""
KryptVM - Luau runtime generator.

Emits the final loader script:
    junk-unicode payload -> de-junk -> 4 decryption layers -> LZ77 inflate
    -> proto parser -> virtual machine with a shuffled opcode dispatch table

Nothing is written to a global; the program only exists decoded in memory.
"""

import random
import re

from .compiler import OP_SIG, OP_ID, OP_NAME, OPCOUNT, ARITH_OPS
from .minify import minify
from .packer import UNI_BASE, escape_unicode

KIND = {'w': 1, 'n': 2, 'c': 3, 'j': 4, 'N': 5}

ARITH_LUA = {
    '+': 'x + y', '-': 'x - y', '*': 'x * y', '/': 'x / y', '%': 'x % y',
    '^': 'x ^ y', '//': 'flr(x / y)', '..': 'x .. y',
    '==': 'x == y', '~=': 'x ~= y', '<': 'x < y', '<=': 'x <= y',
    '>': 'x > y', '>=': 'x >= y',
    '&': 'bit.band(x, y)', '|': 'bit.bor(x, y)', '~': 'bit.bxor(x, y)',
    '<<': 'bit.lshift(x, y)', '>>': 'bit.rshift(x, y)',
}
ARITH_ORDER = ['+', '..', '-', '*', '/', '%', '^', '//', '==', '~=', '<', '<=',
               '>', '>=', '&', '|', '~', '<<', '>>']


def shape_value(name):
    v = 0
    m = 1
    for ch in OP_SIG[name]:
        v += KIND[ch] * m
        m *= 6
    return v


def gen_arith_chain():
    parts = []
    for idx, op in enumerate(ARITH_ORDER):
        cond = 'd == %d' % (ARITH_OPS.index(op) + 1)
        kw = 'if' if idx == 0 else 'elseif'
        parts.append('%s %s then t[a] = %s' % (kw, cond, ARITH_LUA[op]))
    parts.append("else error('bad arithmetic op') end")
    return ' '.join(parts)


def op_impl():
    """Leaf bodies; VARARG/ARITH get register-slot / op-id substitutions."""
    A = gen_arith_chain()
    impl = {}
    impl['NOP'] = 'return pc'
    impl['MOVE'] = 't[a] = t[b] return pc'
    impl['LOADK'] = 'if b < 0 then t[a] = K[-b] else t[a] = t[b] end return pc'
    impl['LOADNI'] = 'for i = 0, b - 1 do t[a + i] = nil end return pc'
    impl['LOADTRUE'] = 't[a] = true return pc'
    impl['LOADFALS'] = 't[a] = false return pc'
    impl['LOADI'] = 't[a] = b return pc'
    impl['ARITH'] = ('local x, y if b < 0 then x = K[-b] else x = t[b] end '
                     'if c < 0 then y = K[-c] else y = t[c] end %s return pc' % A)
    impl['UNARY'] = ('local x if b < 0 then x = K[-b] else x = t[b] end '
                     'if c == 1 then t[a] = -x elseif c == 3 then t[a] = #x '
                     'else t[a] = not x end return pc')
    impl['LEN'] = ('local x if b < 0 then x = K[-b] else x = t[b] end '
                   't[a] = #x return pc')
    impl['CONCAT'] = 'local s = "" for i = 0, b - 1 do s = s .. t[a + i] end t[c] = s return pc'
    impl['NEWTAB'] = 't[a] = {} return pc'
    impl['GETTAB'] = ('local o, kk if b < 0 then o = K[-b] else o = t[b] end '
                      'if c < 0 then kk = K[-c] else kk = t[c] end '
                      't[a] = o[kk] return pc')
    impl['SETTAB'] = ('local o, kk if b < 0 then o = K[-b] else o = t[b] end '
                      'if c < 0 then kk = K[-c] else kk = t[c] end '
                      'o[kk] = t[a] return pc')
    impl['GETG'] = 't[a] = G[K[-b]] return pc'
    impl['SETG'] = 'G[K[-b]] = t[a] return pc'
    impl['GETU'] = 'local h = uv[b + 1] t[a] = h[1][h[2]] return pc'
    impl['SETU'] = 'local h = uv[b + 1] h[1][h[2]] = t[a] return pc'
    impl['VARARG'] = ('local va = t[%(VARREG)d] if b == 255 then local n = va.n '
                      'for i = 1, n do t[a + i - 1] = va[i] end t[0] = n '
                      't[%(TOPREG)d] = a + n - 1 else for i = 1, b do t[a + i - 1] = va[i] end end '
                      'return pc')
    impl['CALL'] = 'return _call(fr, pc, a, b, c, d)'
    impl['RETURN'] = 'return _ret(fr, a, b)'
    impl['SELF'] = ('local o, kk if b < 0 then o = K[-b] else o = t[b] end '
                    'if c < 0 then kk = K[-c] else kk = t[c] end '
                    't[a] = o[kk] t[a + 1] = o return pc')
    impl['CLOSURE'] = 't[a] = _mkc(P[b + 1]) return pc'
    impl['ADDUP'] = '_lastuv[#_lastuv + 1] = {t, b} return pc'
    impl['ADDUPU'] = '_lastuv[#_lastuv + 1] = uv[b + 1] return pc'
    impl['JMP'] = 'return pc + a'
    impl['TEST'] = 'if _tr(t[a]) == (b ~= 0) then return pc + c end return pc'
    impl['FORPREP'] = 'local st = _n(t[a + 2]) t[a] = _n(t[a]) - st return pc + b'
    impl['FORLOOP'] = ('local st = _n(t[a + 2]) local idx = _n(t[a]) + st '
                       'local lim = _n(t[a + 1]) '
                       'if (st >= 0 and idx <= lim) or (st < 0 and idx >= lim) then '
                       't[a] = idx t[a + 3] = idx return pc + b end return pc')
    impl['GENPREP'] = ('local r = _pack(t[a](t[a + 1], t[a + 2])) '
                       'for i = 1, b do t[a + 3 + i - 1] = r[i] end t[a + 2] = r[1] '
                       'if r[1] == nil then return pc + c end return pc')
    impl['GENNEXT'] = ('local r = _pack(t[a](t[a + 1], t[a + 2])) '
                       'for i = 1, b do t[a + 3 + i - 1] = r[i] end t[a + 2] = r[1] '
                       'if r[1] ~= nil then return pc + c end return pc')
    impl['SETLIST'] = ('local tb = t[a] local cnt = c if cnt == 255 then cnt = t[0] end '
                       'for i = 1, cnt do tb[d + i - 1] = t[b + i - 1] end return pc')
    impl['CMPBR'] = ('local x = t[a] local y if b < 0 then y = K[-b] else y = t[b] end '
                     'local code = c local r = _cmp[flr(code / 2)](x, y) '
                     'if code % 2 == 1 then r = not r end '
                     'if r then return pc + d end return pc')
    impl['HALT'] = 'return'
    impl['XORJ'] = 'return pc'
    return impl


# decoy VM handlers: plausible opcode bodies that no opcode id ever selects.
# They exist so that reading the handler array does not reveal the real ISA.
_DECOY_LEAVES = (
    'local x = t[a] if b < 0 then x = K[-b] end t[(c > 0 and c or a) + 1] = x return pc',
    'local s = 0 for i = 0, b - 1 do s = s + (t[a + i] or 0) end t[c] = s % 4096 return pc',
    'local x if b < 0 then x = K[-b] else x = t[b] end t[a] = x == nil and 0 or x return pc + d',
    'local o = t[a] if type_(o) ~= "table" then return pc end t[b] = o[1] t[c] = o[2] return pc',
    'local n = 0 for i = a, a + b - 1 do if _tr(t[i]) then n = n + 1 end end t[c] = n return pc',
    'local s = "" for i = 0, b - 1 do s = s .. tostring(t[a + i]) end t[c] = s return pc',
    'local x, y = t[a], t[b] if x == nil or y == nil then t[c] = 0 else t[c] = x ~= y and 1 or 0 end return pc',
    'local i = a while i <= b do t[i] = nil i = i + 1 end return pc',
    'local r = _cmp[8](t[a], t[b]) if r then return pc + c end return pc',
    't[a] = ({})[b] or c return pc',
    'local u = uv[b + 1] if u then t[a] = u[1][u[2]] end return pc',
    'local v = K[-b] if type_(v) == "string" then t[a] = #v else t[a] = 0 end return pc',
)


def _decoy_leaves(perm, rng):
    """Emit decoy handler slots *after* the real opcode ids, shuffled."""
    out = []
    base = 64
    order = list(range(len(_DECOY_LEAVES)))
    rng.shuffle(order)
    for n, pick in enumerate(order):
        eid = base + n
        body = _DECOY_LEAVES[pick]
        out.append('_F[%d] = function(fr, pc, a, b, c, d) '
                   'local t = fr.t local K = fr.K local P = fr.P local uv = fr.uv '
                   'local G = fr.G %s end' % (eid, body))
    return '\n'.join(out)


def _leafline(eid, name, src):
    return ('_F[%d] = function(fr, pc, a, b, c, d) '
            'local t = fr.t local K = fr.K local P = fr.P local uv = fr.uv '
            'local G = fr.G %s end' % (eid, src))


UNI_POOL = ('┒╫┇╯┄▽▴◨▦╙▾╠╵╨╾▧╀┫◒▇◬◤╮□◤╩╧▸╎┄◳┴╡◲◷┺△◢◡┓┸┞▒▰▼▓┽╤◖─▁┹╵◈╫◫◮┙╕◲╊▝►╓╷┨◗┳▍╲▤▯╿┿'
            '░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀◣◤◥◢▣◈◉◍◐◑◒◓◔◕◆◇'
            '◧◨◩◪◫◬◭◮◯☰☱☲☳☴☵☶☷♠♣♥♦♪♫☼↕↔¶§')

# identifiers inside TEMPLATE that get renamed per file
REG_VARARGS = 201          # register slot holding the vararg pack
REG_TOP = 202              # register slot holding the live top

MANGLED = ['byte', 'sub_', 'char', 'flr', 'unpack_', 'concat_', 'select_', 'type_',
           'tonumber_', 'error_', 'bit', 'pcall_', 'setmetatable_', 'rawset_',
           'rawget_', 'getfenv_', 'next_', '_PAY', '_xs', '_unjunk', '_unxor',
           '_rc4d', '_unsh', '_mkorder', '_merge', '_SB', '_SL', '_SV', '_SW', '_i',
           '_GT', '_SH', '_rot', '_mix', '_hb', '_kdf', '_expk', '_rc4b', '_unch', '_kmk', 'm8', 'm16', 'mr',
           '_celln', '_pp', 'xlat', 'ids',
           '_unadd', '_unrot', '_unnib', '_unlz', '_unshift', '_build', '_SZ',
           '_F', '_pack', '_tr', '_n', '_cmp', '_mkc', '_lastuv', '_call', '_ret',
           '_run', '_GENV', '_mkG', '_main']

LEVELS = {'light': 1, 'heavy': 2, 'insane': 3}
# operand-mask strength per level: 0 none, 1 per-proto, 2 per-field
_MASK_FOR_LEVEL = {1: 0, 2: 1, 3: 2}


_DECOY_SNIPPETS = (
    'local function @F@(a, b)\n'
    '  local x = a * @N1@ + b\n'
    '  for i = 1, @N2@ do x = x - i % 7 end\n'
    '  return x\n'
    'end\n',

    'local function @F@(t)\n'
    '  local s = 0\n'
    '  for k, v in ipairs(t) do s = s + v * @N1@ end\n'
    '  return s, #t\n'
    'end\n',

    'local @F@ = function(a)\n'
    '  local b = "@W1@"\n'
    '  local c = 0\n'
    '  for i = 1, #b do c = c + string.byte(b, i) end\n'
    '  return c + a, b\n'
    'end\n',

    'local function @F@(a, b)\n'
    '  if a > b then return a - b end\n'
    '  return b - a, @N1@\n'
    'end\n',

    'local function @F@(a)\n'
    '  local t = {}\n'
    '  for i = 1, @N2@ do t[i] = (a + i) * @N1@ end\n'
    '  local s = ""\n'
    '  for i = 1, #t do s = s .. tostring(t[i] % 10) end\n'
    '  return s\n'
    'end\n',
)


def build_decoy_protos(n, seed):
    """Compile n small programs that look like real work but are never called."""
    from .compiler import compile_ast as _ca
    from .frontend import parse as _parse
    r = random.Random(seed ^ 0xDEC0DE)
    out = []
    for i in range(n):
        src = r.choice(_DECOY_SNIPPETS)
        src = (src.replace('@F@', 'fn%d' % (i + 1))
                  .replace('@N1@', str(r.randint(2, 40)))
                  .replace('@N2@', str(r.randint(3, 25)))
                  .replace('@W1@', r.choice(('alpha', 'beta', 'gamma', 'delta',
                                             'omega', 'zeta', 'sigma'))))
        try:
            prs = _ca(_parse(src))
        except Exception:
            continue
        out.extend(prs)
    return out


def _reindex(pr, delta, remap=None):
    """Offset a freshly compiled proto's indices so it can be appended."""
    from .compiler import OP_SIG
    pr.index = pr.index + delta
    if remap is not None:
        pr.index = remap(pr.index)
    for i, ins in enumerate(pr.code):
        if ins[0] == 'CLOSURE':
            tgt = ins[2] + delta
            pr.code[i] = ('CLOSURE', ins[1], remap(tgt) if remap else tgt)
    return pr


def mix_protos(real, seed, ndecoy=None):
    """Append decoy protos and shuffle the whole set.

    Returns (protos_in_slot_order, slot_of_each_original_index).
    The loader never learns the entry slot from the file: it is derived from
    the chain secret and the shuffled layout.
    """
    r = random.Random(seed ^ 0x9E3779B9)
    if ndecoy is None:
        ndecoy = max(2, min(6, len(real) // 2 + 2))
    decoys = build_decoy_protos(ndecoy, seed)
    allp = list(real)
    delta = len(real)
    for d in decoys:
        allp.append(_reindex(d, delta))
    order = list(range(len(allp)))
    r.shuffle(order)                     # order[slot] = original index
    inv = [0] * len(allp)
    for slot, orig in enumerate(order):
        inv[orig] = slot
    shuffled = [allp[orig] for orig in order]
    return shuffled, inv


class RuntimeGen:
    def __init__(self, protos, seed=None, raw_unicode=False, obf_level='heavy',
                 minify=True):
        self.protos = protos
        self.seed = seed if seed is not None else random.getrandbits(31)
        self.rng = random.Random(self.seed)
        self.raw_unicode = raw_unicode
        self.obf_level = obf_level
        self.minify = minify
        self.level = LEVELS.get(obf_level, 2) if isinstance(obf_level, str) else int(obf_level or 2)
        # real opcode permutation: a full shuffle of the 6-bit opcode space
        ids = list(range(1, 64))
        self.rng.shuffle(ids)
        self.perm = {name: ids[i] for i, name in enumerate(OP_NAME)}
        # per-file random names for every loader-internal identifier
        self.names = {}
        used = set()
        for nm in MANGLED:
            while True:
                cand = '_' + ''.join(self.rng.choice('abcdefghijklmnopqrstuvwxyz'
                                                     'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
                                     for _ in range(self.rng.randint(5, 8)))
                if cand not in used:
                    used.add(cand)
                    break
            self.names[nm] = cand
        # never rename field accesses such as string.byte / table.concat
        self._rename_re = re.compile(r'(?<![\w.])(?:%s)(?![\w])' % '|'.join(
            re.escape(k) for k in sorted(MANGLED, key=len, reverse=True)))

    def rename(self, text):
        return self._rename_re.sub(lambda m: self.names[m.group(0)], text)

    def _chunks(self, chunks):
        if self.raw_unicode:
            lit = chunks
        else:
            lit = [escape_unicode(c) for c in chunks]
        return ', '.join('"%s"' % c for c in lit)

    def gen(self, junk_text, meta):
        # payload chunks are split into two arrays and interleaved at runtime,
        # so no single literal in the file is a usable payload fragment
        chunks = [junk_text[i:i + 768] for i in range(0, len(junk_text), 768)] or ['']
        even, odd = chunks[0::2], chunks[1::2]
        while len(odd) < len(even):
            odd.append('')
        ps1 = self._chunks(even)
        ps3 = self._chunks(odd)
        r = random.Random(self.seed ^ 0xFACADE)
        ps2 = ', '.join('"%s"' % ''.join(r.choice(UNI_POOL)
                                         for _ in range(r.randint(48, 96)))
                        for _ in range(3))           # decoy payload, never referenced

        impls = {k: (v % {'VARREG': REG_VARARGS, 'TOPREG': REG_TOP}
                     if '%(' in v else v) for k, v in op_impl().items()}
        names = list(OP_NAME)
        self.rng.shuffle(names)                     # handler order != semantic order
        leaves = '\n'.join(_leafline(self.perm[n], n, impls[n]) for n in names)
        leaves += '\n' + _decoy_leaves(self.perm, self.rng)
        shapes = '\n'.join('_SZ[%d] = %d' % (self.perm[n], shape_value(n)) for n in names)

        body = TEMPLATE % {
            'PS1': ps1, 'PS2': ps2, 'PS3': ps3,
            'MASTER': meta['master'],
            'NCELL1': meta['ncells1'], 'NCELL2': meta['ncells2'],
            'INTEG': meta['integ'], 'RAWL': meta['rawl'],
            'MASKMODE': meta.get('mask_mode', _MASK_FOR_LEVEL.get(self.level, 2)),
            'ENTRY': meta['entry'],
            'VARREG': REG_VARARGS, 'TOPREG': REG_TOP,
            'K0': meta['keys'][0], 'K1': meta['keys'][1],
            'K2': meta['keys'][2], 'K3': meta['keys'][3],
            'LEN': meta['enc_len'], 'NEVEN': meta['n_even'],
            'SHSEED': meta['shuf_seed'], 'KSEED': meta['key_seed'],
            'UBASE': UNI_BASE,
            'SHAPES': shapes,
            'LEAVES': leaves,
            'CALLID': self.perm['CALL'],
            'RETID': self.perm['RETURN'],
            'HALTID': self.perm['HALT'],
            'DECOYS': self.decoys(),
            'DEADCODE': self.dead_code(),
            'SEED': self.seed,
        }
        return self._finish(body)

    def _finish(self, body):
        body = self.rename(body)
        if self.minify:
            body = minify(body)
        return self.junk_wall() + body + self.junk_tail()

    def gen_pretty(self, junk_text, meta):
        """Same chunk, human-readable (for debugging only)."""
        keep = self.minify
        self.minify = False
        try:
            return self.gen(junk_text, meta).replace(self.junk_wall(), '', 1) \
                       .replace(self.junk_tail(), '')
        finally:
            self.minify = keep

    # ---------------- junk / decoy layers ----------------
    def _wall_block(self, r):
        tag = ''.join(r.choice('0123456789abcdef') for _ in range(r.randint(32, 56)))
        return '--[[ %s :: %s ]]' % (
            tag, ''.join(r.choice(UNI_POOL) for _ in range(r.randint(180, 420))))

    def junk_wall(self):
        """Dense single-line unicode header: reads like a packed asset dump."""
        r = random.Random(self.seed ^ 0xDEAD)
        return ''.join(self._wall_block(r) for _ in range(3 + 3 * self.level))

    def junk_tail(self):
        r = random.Random(self.seed ^ 0xB0A7)
        return ''.join(self._wall_block(r) for _ in range(1 + 2 * self.level))

    def decoys(self):
        r = random.Random(self.seed ^ 0xBEEF)
        out = []
        for _ in range(4 * self.level):
            nm = '_%s' % ''.join(r.choice('abcdef0123456789') for _ in range(9))
            out.append('local %s = "%s" -- %s' % (
                nm, ''.join(r.choice(UNI_POOL) for _ in range(r.randint(6, 18))),
                ''.join(r.choice(UNI_POOL) for _ in range(r.randint(3, 9)))))
        return '\n'.join(out)

    def dead_code(self):
        """Plausible-looking but unreachable functions / branches, never executed."""
        r = random.Random(self.seed ^ 0xC0FFEE)
        jn = self._junk_names(r, 64)
        out = []

        # 1. dead helper functions that mirror the loader's own shape
        for i in range(4 * self.level):
            fn = jn[i]
            a1, a2, a3 = jn[(12 + i) % 64], jn[(16 + i) % 64], jn[(20 + i) % 64]
            body = r.choice([
                'local o = {} for k = 1, #%s do o[k] = string.byte(%s, k) end return o' % (a1, a1),
                'local s = 0 for k = 1, #%s do s = (s * 31 + string.byte(%s, k)) %% 4294967296 end return s' % (a1, a1),
                'local o, j = {}, 0 for k = 1, #%s, 3 do j = j + 1 o[j] = string.sub(%s, k, k + 2) end return table.concat(o, %s)' % (a1, a1, a3),
                'if #%s == 0 then return %s end return string.sub(%s, 2, -1) .. string.sub(%s, 1, 1)' % (a1, a2, a1, a1),
            ])
            out.append('local function %s(%s, %s, %s) %s end' % (fn, a1, a2, a3, body))

        # 2. dead constant tables full of unicode garbage
        for i in range(2 * self.level):
            nm = jn[(8 + i) % 64]
            items = ', '.join('"%s"' % ''.join(r.choice(UNI_POOL) for _ in range(r.randint(4, 14)))
                              for _ in range(r.randint(5, 12)))
            out.append('local %s = { %s }' % (nm, items))

        # 3. permanently-false branches (never taken, but fully parsed + bytecode'd)
        for i in range(3 * self.level):
            nm = jn[(4 + i) % 64]
            junkline = ''.join(r.choice(UNI_POOL) for _ in range(r.randint(30, 70)))
            out.append('if #"%s" == -1 then local %s = "%s" ' 
                       'for k = 1, #%s do %s = %s .. string.sub("%s", k, k) end print(%s) end'
                       % (junkline, nm, junkline, nm, nm, nm, junkline, nm))

        # 4. decoy "decryption" call sites that are only referenced from dead code
        out.append('if (0x5eed %% 7) > 900 then %s(nil) end' % jn[0])
        return '\n'.join(out)

    def _junk_names(self, r, n):
        out = []
        for _ in range(n):
            out.append('_%s' % ''.join(r.choice('abcdefghijklmnopqrstuvwxyz_') for _ in range(r.randint(6, 11))))
        return out


TEMPLATE = r'''%(DECOYS)s
%(DEADCODE)s
local byte, sub_ = string.byte, string.sub
local char = string.char
local flr = math.floor
local unpack_ = table.unpack or unpack
local concat_ = table.concat
local select_, type_, tonumber_, error_ = select, type, tonumber, error
local bit = bit32
local pcall_ = pcall
local setmetatable_, rawset_, rawget_ = setmetatable, rawset, rawget
local getfenv_ = getfenv
local next_ = next
local _SH = _G or {}

-- standard globals captured by *name* in this chunk's own scope. Roblox does
-- not expose getfenv to scripts and its _G is a separate shared table, so
-- these literals are the only reliable way to reach the script environment.
local _GT = {
  assert = assert, bit32 = bit32, collectgarbage = collectgarbage,
  coroutine = coroutine, delay = delay, error = error, game = game,
  getmetatable = getmetatable, ipairs = ipairs, loadstring = loadstring,
  math = math, newproxy = newproxy, next = next, os = os, pairs = pairs,
  pcall = pcall, print = print, rawequal = rawequal, rawget = rawget,
  rawset = rawset, require = require, script = script, select = select,
  setmetatable = setmetatable, shared = shared, spawn = spawn, string = string,
  table = table, task = task, tick = tick, time = time, tonumber = tonumber,
  tostring = tostring, type = type, typeof = typeof, unpack = unpack,
  utf8 = utf8, wait = wait, warn = warn, workspace = workspace, xpcall = xpcall,
  Instance = Instance, Vector2 = Vector2, Vector3 = Vector3, CFrame = CFrame,
  Color3 = Color3, BrickColor = BrickColor, UDim = UDim, UDim2 = UDim2,
  Rect = Rect, Region3 = Region3, Ray = Ray, TweenInfo = TweenInfo, Font = Font,
  NumberRange = NumberRange, NumberSequence = NumberSequence, DateTime = DateTime,
  ColorSequence = ColorSequence, PhysicalProperties = PhysicalProperties,
  RaycastParams = RaycastParams, OverlapParams = OverlapParams, Random = Random,
  Enum = Enum, buffer = buffer, vector = vector,
}
_GT._G = _SH

local _SB = %(PS2)s -- resource block
local _SV = { %(PS1)s }
local _SW = { %(PS3)s }
local _SL = {}
for _i = 1, #_SV do _SL[#_SL + 1] = _SV[_i] _SL[#_SL + 1] = _SW[_i] end
local _PAY = concat_(_SL)

local function _xs(s)
  s = bit.bxor(s, bit.lshift(s, 13))
  s = bit.bxor(s, bit.rshift(s, 17))
  s = bit.bxor(s, bit.lshift(s, 5))
  return bit.band(s, 4294967295)
end

local function _unjunk(s, base)
  local n, out, j, i = #s, {}, 0, 1
  while i <= n do
    local b1, b2, b3 = byte(s, i, i + 2)
    j = j + 1
    out[j] = char(bit.band((b1 %% 16) * 4096 + (b2 %% 64) * 64 + (b3 %% 64) - base, 255))
    i = i + 3
  end
  return concat_(out)
end

local function _rc4d(s, kseed)
  local kb, r, klen = {}, kseed, 16
  if r == 0 then r = 1234567 end
  for i = 1, klen do
    r = _xs(r)
    kb[i] = r %% 256
  end
  local S = {}
  for i = 0, 255 do S[i] = i end
  local j = 0
  for i = 0, 255 do
    j = (j + S[i] + kb[(i %% klen) + 1]) %% 256
    local t = S[i] S[i] = S[j] S[j] = t
  end
  local out, a, b, n = {}, 0, 0, #s
  for i = 1, n do
    a = (a + 1) %% 256
    b = (b + S[a]) %% 256
    local t = S[a] S[a] = S[b] S[b] = t
    out[i] = char(bit.bxor(byte(s, i), S[(S[a] + S[b]) %% 256]))
  end
  return concat_(out)
end

local function _unsh(s, seed)
  local nb = flr(#s / 64)
  local ord = {}
  for i = 1, nb do ord[i] = i end
  local r = seed
  if r == 0 then r = 1234567 end
  for i = nb, 2, -1 do
    r = _xs(r)
    local j = r %% i + 1
    ord[i], ord[j] = ord[j], ord[i]
  end
  local blk = {}
  for i = 1, nb do blk[i] = sub_(s, (i - 1) * 64 + 1, i * 64) end
  local parts = {}
  for dst = 1, nb do parts[ord[dst]] = blk[dst] end
  return concat_(parts)
end

local function _merge(s, neven)
  local out, ev, od = {}, 1, neven + 1
  for i = 1, #s do
    if i %% 2 == 1 then
      out[i] = char(byte(s, ev))
      ev = ev + 1
    else
      out[i] = char(byte(s, od))
      od = od + 1
    end
  end
  return concat_(out)
end

local function _rot(x, n)
  return bit.bor(bit.lshift(x, n), bit.rshift(x, 32 - n))
end

local function _mix(h, v)
  h = bit.bxor(h, v)
  h = bit.bxor(h, _rot(h, 7))
  h = (h + 2654435769) %% 4294967296
  h = bit.bxor(h, bit.rshift(h, 11))
  h = bit.bxor(h, _rot(h, 13))
  return h
end

local function _hb(s, seed)
  local h = seed
  for i = 1, #s do h = _mix(h, byte(s, i)) end
  return h
end

local function _kdf(m, i, p)
  return _mix(_mix(bit.bxor(m, i + 1), p), i + 23130)
end

local function _expk(seed, n)
  local r = seed
  if r == 0 then r = 1234567 end
  local out = {}
  for i = 1, n do
    r = _xs(r)
    out[i] = r %% 256
  end
  return out
end

local function _rc4b(s, key)
  local S, klen = {}, #key
  for i = 0, 255 do S[i] = i end
  local j = 0
  for i = 0, 255 do
    j = (j + S[i] + key[(i %% klen) + 1]) %% 256
    local t = S[i] S[i] = S[j] S[j] = t
  end
  local out, a, b, n = {}, 0, 0, #s
  for i = 1, n do
    a = (a + 1) %% 256
    b = (b + S[a]) %% 256
    local t = S[a] S[a] = S[b] S[b] = t
    out[i] = char(bit.bxor(byte(s, i), S[(S[a] + S[b]) %% 256]))
  end
  return concat_(out)
end

local function _celln(master, i, axis)
  return 384 + (_kdf(master, i, axis) %% 256)
end

local function _unch(s, master, ncell, axis)
  local out, pos, prev = {}, 1, _kdf(master, 65261, axis)
  for i = 0, ncell - 1 do
    local n = _celln(master, i, axis)
    local cell = sub_(s, pos, pos + n - 1)
    pos = pos + n
    local plain = _rc4b(cell, _expk(_kdf(master, bit.bor(bit.lshift(i, 8), bit.band(axis, 255)), prev), 16))
    out[#out + 1] = plain
    prev = _hb(plain, prev)
  end
  return concat_(out)
end

local function _unxor(s, key)
  local out, r, n = {}, key, #s
  for i = 1, n do
    r = _xs(r)
    out[i] = char(bit.bxor(byte(s, i), r %% 256))
  end
  return concat_(out)
end

local function _unadd(s, key)
  local out, r, n = {}, key, #s
  for i = 1, n do
    r = _xs(r)
    out[i] = char((byte(s, i) - r %% 256) %% 256)
  end
  return concat_(out)
end

local function _unrot(s, key)
  local out, n = {}, #s
  for i = 1, n do
    local r = ((i - 1) * 7 + key) %% 8
    local v = byte(s, i)
    if r > 0 then
      v = bit.band(bit.bor(bit.rshift(v, r), bit.lshift(v, 8 - r)), 255)
    end
    out[i] = char(v)
  end
  return concat_(out)
end

local function _unnib(s)
  local out = {}
  for i = 1, #s do
    local v = byte(s, i)
    out[i] = char((v %% 16) * 16 + flr(v / 16) %% 16)
  end
  return concat_(out)
end

local function _unlz(s)
  local out, j, n, i = {}, 0, #s, 1
  while i <= n do
    local flags = byte(s, i)
    i = i + 1
    for _ = 1, 8 do
      if i > n then break end
      if flags %% 2 == 1 then
        local b1, b2, b3 = byte(s, i, i + 2)
        i = i + 3
        local d = b1 * 256 + b2 + 1
        local ln = b3 + 3
        local st = j - d + 1
        for k = 0, ln - 1 do
          j = j + 1
          out[j] = out[st + k]
        end
      else
        j = j + 1
        out[j] = char(byte(s, i))
        i = i + 1
      end
      flags = flr(flags / 2)
    end
  end
  return concat_(out)
end

local function _unshift(txt)
  local out = {}
  for i = 1, #txt do
    local c = sub_(txt, i, i)
    local d = tonumber_(c)
    if d then
      out[i] = char(48 + (d + 7) %% 10)
    else
      out[i] = c
    end
  end
  return concat_(out)
end

local _SZ = {}
local _F = {}
local _pack, _tr, _n, _cmp, _mkc, _lastuv, _call, _ret, _run, _GENV

%(SHAPES)s

%(LEAVES)s

local function _kmk(ct, master, pi)
  local cache, has = {}, {}
  return function(_, i)
    if i < 0 then i = -i end
    if has[i] then return cache[i] end
    has[i] = true
    local c = ct[i]
    if c == nil then return nil end
    local tag = c[1]
    if tag == 0 then return nil end
    if tag == 1 then cache[i] = false return false end
    if tag == 2 then cache[i] = true return true end
    if tag == 3 then
      local n, buf = c[2], c[3]
      local st = _expk(_kdf(master, 512 + (i - 1) + 256 * pi, n), n)
      local out = {}
      for j = 1, n do out[j] = char(bit.bxor(buf[j], st[j])) end
      c[2], c[3] = 0, nil            -- wipe the ciphertext once used
      local txt = _unshift(concat_(out))
      local v
      if txt == 'i' then
        v = math.huge
      elseif txt == '-i' then
        v = -math.huge
      elseif txt == 'n' then
        v = 0 / 0
      else
        v = tonumber_(txt)
      end
      cache[i] = v
      return v
    end
    local l1, b1, l2, b2 = c[2], c[3], c[4], c[5]
    local n = l1 + l2
    local st = _expk(_kdf(master, 1024 + (i - 1) + 256 * pi, l1 * 65536 + l2), n)
    local out = {}
    for j = 1, n do
      local b
      if j <= l1 then b = b1[j] else b = b2[j - l1] end
      out[j] = char(bit.bxor(b, st[j]))
    end
    c[2], c[3], c[4], c[5] = 0, nil, 0, nil
    local str = concat_(out)
    cache[i] = str
    return str
  end
end

local _MM = %(MASKMODE)d

local function _build(master)
  local blob = _unjunk(_PAY, %(UBASE)d)
  blob = _rc4d(blob, %(KSEED)d)
  blob = _unsh(blob, %(SHSEED)d)
  blob = _merge(sub_(blob, 1, %(LEN)d), %(NEVEN)d)
  blob = _unnib(_unrot(_unadd(_unxor(blob, %(K0)d), %(K1)d), %(K2)d))
  blob = _unch(blob, master, %(NCELL2)d, 48879)
  blob = _unch(blob, master, %(NCELL1)d, 659918)
  blob = _unlz(blob)
  if bit.bxor(_hb(blob, 12648430), _kdf(master, 30583, %(RAWL)d)) ~= %(INTEG)d then return end
  local pos, acc, nb = 1, 0, 0
  local function rd(bits)
    while nb < bits do
      acc = acc + byte(blob, pos) * 2 ^ nb
      pos = pos + 1
      nb = nb + 8
    end
    local m = 2 ^ bits
    local v = acc %% m
    acc = (acc - v) / m
    nb = nb - bits
    return v
  end
  local nproto = rd(16)
  local protos = {}
  local function _pp(pi)
    local r = _kdf(master, pi, 4080)
    if r == 0 then r = 1234567 end
    local ids = {}
    for i = 1, 64 do ids[i] = i - 1 end
    for i = 64, 2, -1 do
      r = _xs(r)
      local j = r %% i + 1
      ids[i], ids[j] = ids[j], ids[i]
    end
    local xlat = {}
    for k = 1, 64 do xlat[ids[k]] = k end
    return xlat
  end
  for pi = 1, nproto do
    local pr = {uv = {}}
    pr.np = rd(8)
    pr.nv = rd(8)
    local nup = rd(8)
    for i = 1, nup do
      local uk = rd(8)
      local ur = rd(8)
      pr.uv[i] = {uk, ur}
    end
    local nins = rd(16)
    local code = {}
    local ci = 0
    local xlat = _pp(pi - 1)
    -- operand masks: derived from the chain secret, never stored in the file
    local m8, m16
    if _MM == 0 then
      m8 = function() return 0 end
      m16 = function() return 0 end
    elseif _MM == 1 then
      local kk = _xs(_kdf(master, pi - 1, 20817))
      m8 = function() return kk %% 256 end
      m16 = function() return kk %% 65536 end
    else
      local mr = _kdf(master, pi - 1, nins)
      if mr == 0 then mr = 1234567 end
      m8 = function() mr = _xs(mr) return mr %% 256 end
      m16 = function() mr = _xs(mr) return mr %% 65536 end
    end
    for _ = 1, nins do
      local op = xlat[rd(6)] or 0
      local shape = _SZ[op]
      local k = 0
      while shape > 0 do
        local kind = shape %% 6
        shape = flr(shape / 6)
        k = k + 1
        local v
        if kind == 1 or kind == 2 then
          v = bit.bxor(rd(8), m8())
        elseif kind == 3 then
          if rd(1) == 1 then
            v = 0 - (bit.bxor(rd(16), m16()) + 1)
          else
            v = bit.bxor(rd(8), m8())
          end
        elseif kind == 4 then
          v = bit.bxor(rd(16), m16())
          if v >= 32768 then v = v - 65536 end
          v = v * 6
        else
          v = bit.bxor(rd(16), m16())
        end
        code[ci + k + 1] = v
      end
      for m = k + 1, 5 do code[ci + m + 1] = 0 end
      code[ci + 1] = op
      ci = ci + 6
    end
    pr.code = code
    local nk = rd(16)
    local ct = {}
    for i = 1, nk do
      local tag = rd(8)
      if tag == 0 or tag == 1 or tag == 2 then
        ct[i] = {tag}
      elseif tag == 3 then
        local len = rd(16)
        local buf = {}
        for j = 1, len do buf[j] = rd(8) end
        ct[i] = {3, len, buf}
      else
        local l1 = rd(16)
        local b1 = {}
        for j = 1, l1 do b1[j] = rd(8) end
        local l2 = rd(16)
        local b2 = {}
        for j = 1, l2 do b2[j] = rd(8) end
        ct[i] = {4, l1, b1, l2, b2}
      end
    end
    pr.ct = ct
    pr.k = setmetatable_({}, {__index = _kmk(ct, master, pi)})
    pr.p = protos
    protos[pi] = pr
  end
  return protos
end

_pack = function(...)
  return {n = select_("#", ...), ...}
end

_tr = function(v)
  return v ~= nil and v ~= false
end

_n = function(v)
  local n = tonumber_(v)
  if n == nil then
    error_((v == nil and 'attempt to perform arithmetic on a nil value')
      or ('attempt to perform arithmetic on a ' .. type_(v) .. ' value'), 2)
  end
  return n
end

_cmp = {
  [8] = function(x, y) return x == y end,
  [9] = function(x, y) return x ~= y end,
  [10] = function(x, y) return x < y end,
  [11] = function(x, y) return x <= y end,
  [12] = function(x, y) return x > y end,
  [13] = function(x, y) return x >= y end,
}

_mkc = function(pr)
  local uv = {}
  _lastuv = uv
  return function(...)
    return _run(pr, uv, ...)
  end
end

_call = function(fr, pc, a, b, c, d)
  local t = fr.t
  if b == 255 then b = t[%(TOPREG)d] - a end
  local r = _pack(t[a](unpack_(t, a + 1, a + b)))
  if c == 255 then
    for i = 1, r.n do t[d + i - 1] = r[i] end
    t[0] = r.n
    t[%(TOPREG)d] = d + r.n - 1
  else
    for i = 1, c do t[d + i - 1] = r[i] end
  end
  return pc
end

_ret = function(fr, a, b)
  local t = fr.t
  if b == 255 then b = t[%(TOPREG)d] - a + 1 end
  if b <= 0 then return end
  return unpack_(t, a, a + b - 1)
end

_run = function(pr, uv, ...)
  local C = pr.code
  local t = {0}
  local np = pr.np
  local argc = select_("#", ...)
  local args = {...}
  for i = 1, np do t[i] = args[i] end
  if pr.nv ~= 0 then
    local va = {n = argc - np}
    for i = np + 1, argc do va[i - np] = args[i] end
    t[%(VARREG)d] = va
  end
  t[%(TOPREG)d] = np
  local fr = {t = t, K = pr.k, P = pr.p, uv = uv, G = _GENV}
  local pc = 1
  while true do
    local op = C[pc]
    local a = C[pc + 1]
    local b = C[pc + 2]
    local c = C[pc + 3]
    local d = C[pc + 4]
    local npc = pc + 6
    if op == %(CALLID)d then
      if b == 255 then b = t[%(TOPREG)d] - a end
      local r = _pack(t[a](unpack_(t, a + 1, a + b)))
      if c == 255 then
        for i = 1, r.n do t[d + i - 1] = r[i] end
        t[0] = r.n
        t[%(TOPREG)d] = d + r.n - 1
      else
        for i = 1, c do t[d + i - 1] = r[i] end
      end
      pc = npc
    elseif op == %(RETID)d then
      if b == 255 then b = t[%(TOPREG)d] - a + 1 end
      if b <= 0 then return end
      return unpack_(t, a, a + b - 1)
    elseif op == %(HALTID)d then
      return
    else
      pc = _F[op](fr, npc, a, b, c, d)
    end
  end
end

local function _mkG()
  local realG
  if getfenv_ then
    pcall_(function()
      local e = getfenv_(1)
      if type_(e) == 'table' then realG = e end
    end)
  end
  local G = {}
  return setmetatable_(G, {
    __index = function(_, key)
      local val
      if realG then val = realG[key] end        -- script environment (non-raw:
      if val == nil then val = _GT[key] end     -- Roblox serves globals via __index)
      if val == nil then val = _SH[key] end     -- _G / shared table
      return val
    end,
    __newindex = function(_, key, val)
      rawset_(G, key, val)
      if realG then pcall_(function() realG[key] = val end) end
      pcall_(function() _SH[key] = val end)
    end,
  })
end

local function _main(...)
  local protos = _build(%(MASTER)d)
  _GENV = _mkG()
  local uv = {}
  _lastuv = uv
  local fn = function(...)
    return _run(protos[%(ENTRY)d], uv, ...)
  end
  return fn(...)
end

return _main(...)
'''


def generate_with_meta(protos, seed=None, raw_unicode=False, obf_level='heavy',
                      minify_output=True):
    """Full pipeline: shuffle+decoys -> serialise -> chain -> layers -> loader."""
    from .packer import pack
    gen = RuntimeGen(protos, seed, raw_unicode, obf_level, minify=minify_output)
    shuffled, inv = mix_protos(protos, gen.seed)
    meta_extra = {'proto_map': inv, 'entry': inv[0] + 1,
                  'nprotos': len(shuffled), 'real_protos': len(protos),
                  'mask_mode': _MASK_FOR_LEVEL.get(gen.level, 2)}
    txt, meta = pack(shuffled, gen.seed, gen.perm, extra=meta_extra)
    meta.update(meta_extra)
    src = gen.gen(txt, meta) if minify_output else gen.gen_pretty(txt, meta)
    return src, meta


def generate(protos, seed=None, raw_unicode=False, obf_level='heavy', minify_output=True):
    return generate_with_meta(protos, seed, raw_unicode, obf_level, minify_output)[0]
