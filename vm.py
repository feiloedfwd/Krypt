"""
KryptVM - reference VM in Python.

This mirrors the Luau runtime byte-for-byte in semantics; it exists so the
compiler can be tested quickly without booting a Lua interpreter.
Register file layout: t[0] = rcnt, t[1..200] = registers, t[201] = varargs, t[202] = top.
"""

import math as _math
from .compiler import ARITH_OPS, UNARY_ID, K


class Table:
    __slots__ = ('h', 'mt')

    def __init__(self, arr=None, hash_=None):
        self.h = {}
        self.mt = None
        if arr:
            for i, v in enumerate(arr):
                self.h[i + 1] = v
        if hash_:
            self.h.update(hash_)

    def get(self, k):
        return self.h.get(k)

    def set(self, k, v):
        if v is None:
            self.h.pop(k, None)
        else:
            self.h[k] = v

    def length(self):
        n = 0
        while (n + 1) in self.h:
            n += 1
        return n

    def __repr__(self):
        return 'Table(%r)' % (self.h,)


class Closure:
    __slots__ = ('proto', 'upvals', 'renv')

    def __init__(self, proto, renv):
        self.proto = proto
        self.upvals = []
        self.renv = renv


class Holder:
    __slots__ = ('env', 'i')

    def __init__(self, env, i):
        self.env = env
        self.i = i


class VMError(Exception):
    pass


DEBUG = False


class VM:
    def __init__(self, env=None):
        self.env = env if env is not None else {}
        self.protos = []
        self.helpers = {}
        self.MAXDEPTH = 190

    # ---- value helpers ---------------------------------------------------
    @staticmethod
    def truth(v):
        return not (v is None or v is False)

    def tostr(self, v):
        if v is None:
            return 'nil'
        if v is True:
            return 'true'
        if v is False:
            return 'false'
        if isinstance(v, float):
            if v == int(v) and abs(v) < 1e15:
                return str(int(v))
            return repr(v)
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return v
        if isinstance(v, Table):
            return 'table: 0x%08x' % (id(v) & 0xffffffff)
        if isinstance(v, (Closure,)):
            return 'function: 0x%08x' % (id(v) & 0xffffffff)
        return str(v)

    def tonum(self, v):
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.strip())
            except ValueError:
                return None
        return None

    # ---- upvalues --------------------------------------------------------
    def upval_get(self, clos, i):
        h = clos.upvals[i]
        return h.env[h.i]

    def upval_set(self, clos, i, v):
        h = clos.upvals[i]
        h.env[h.i] = v

    # ---- calling into the VM --------------------------------------------
    def call(self, fn, args):
        """Call a VM value (closure or host callable). Returns tuple of values."""
        if isinstance(fn, Closure):
            if len(args) != fn.proto.nparams and not fn.proto.vararg:
                pass  # Lua fills nils / drops extras
            return self.execute(fn, args)
        if callable(fn):
            r = fn(*args)
            if isinstance(r, tuple):
                return r
            if r is None:
                return ()
            return (r,)
        raise VMError('attempt to call a %s value' % type(fn).__name__)

    def execute(self, clos, args):
        t = [None] * 203
        t[0] = 0
        t[202] = 0
        proto = clos.proto
        code = proto.code
        k = proto.consts
        uv = clos.upvals
        npar = proto.nparams
        for i in range(npar):
            if i < len(args):
                t[i + 1] = args[i]
        varargs = args[npar:] if proto.vararg else ()
        pc = 0

        def res(op):
            return k[op.i] if isinstance(op, K) else t[op]

        while True:
            ins = code[pc]
            op = ins[0]
            if DEBUG:
                print('%s[%d] %-10s %s | t=%s' % (proto.name, pc, op, list(ins[1:]),
                      [None if x == 0 else x for x in t[1:20]]))
            pc += 1
            if op == 'MOVE':
                t[ins[1]] = t[ins[2]]
            elif op == 'LOADK':
                t[ins[1]] = k[ins[2].i]
            elif op == 'LOADNI':
                for i in range(ins[2]):
                    t[ins[1] + i] = None
            elif op == 'LOADTRUE':
                t[ins[1]] = True
            elif op == 'LOADFALS':
                t[ins[1]] = False
            elif op == 'LOADI':
                t[ins[1]] = float(ins[2])
            elif op == 'ARITH':
                a, b, cc, d = ins[1], ins[2], ins[3], ins[4]
                t[a] = self.arith(ARITH_OPS[d - 1], res(b), res(cc))
            elif op == 'UNARY':
                a, b, d = ins[1], ins[2], ins[3]
                t[a] = self.unary(d, res(b))
            elif op == 'LEN':
                t[ins[1]] = float(res(ins[2]).length() if isinstance(res(ins[2]), Table)
                                 else len(res(ins[2])))
            elif op == 'CONCAT':
                a, n, dest = ins[1], ins[2], ins[3]
                s = ''.join(self.tostr(t[a + i]) for i in range(n))
                t[dest] = s
            elif op == 'NEWTAB':
                t[ins[1]] = Table()
            elif op == 'GETTAB':
                obj = res(ins[2])
                key = res(ins[3])
                t[ins[1]] = self.index(obj, key)
            elif op == 'SETTAB':
                obj = res(ins[2])
                key = res(ins[3])
                self.setindex(obj, key, t[ins[1]])
            elif op == 'GETG':
                t[ins[1]] = self.env.get(k[ins[2].i])
            elif op == 'SETG':
                self.env[k[ins[2].i]] = t[ins[1]]
            elif op == 'GETU':
                t[ins[1]] = self.upval_get(clos, ins[2])
            elif op == 'SETU':
                self.upval_set(clos, ins[2], t[ins[1]])
            elif op == 'VARARG':
                a, cnt = ins[1], ins[2]
                if cnt == 255:
                    for i, v in enumerate(varargs):
                        t[a + i] = v
                    t[0] = float(len(varargs))
                    t[202] = a + len(varargs) - 1
                else:
                    for i in range(cnt):
                        t[a + i] = varargs[i] if i < len(varargs) else None
            elif op == 'CALL':
                a, nargs, nres, dest = ins[1], ins[2], ins[3], ins[4]
                if nargs == 255:
                    nargs = int(t[202]) - a
                fn = t[a]
                cargs = [t[a + 1 + i] for i in range(max(0, nargs))]
                rets = self.call(fn, cargs)
                if nres == 255:
                    for i, v in enumerate(rets):
                        t[dest + i] = v
                    t[0] = float(len(rets))
                    t[202] = dest + len(rets) - 1
                else:
                    for i in range(nres):
                        t[dest + i] = rets[i] if i < len(rets) else None
            elif op == 'RETURN':
                a, cnt = ins[1], ins[2]
                if cnt == 255:
                    cnt = (int(t[202]) - a + 1) if t[202] >= a else 0
                return tuple(t[a + i] for i in range(cnt))
            elif op == 'SELF':
                a, b, kk = ins[1], ins[2], ins[3]
                obj = res(b) if not isinstance(b, K) else k[b.i]
                t[a] = self.index(obj, res(kk))
                t[a + 1] = obj
            elif op == 'CLOSURE':
                cl = Closure(self.protos[ins[2]], t)
                self.lastclos = cl
                t[ins[1]] = cl
            elif op == 'ADDUP':
                self.lastclos.upvals.append(Holder(t, ins[2]))
            elif op == 'ADDUPU':
                self.lastclos.upvals.append(clos.upvals[ins[2]])
            elif op == 'JMP':
                pc += ins[1]
            elif op == 'TEST':
                a, want, off = ins[1], ins[2], ins[3]
                if self.truth(t[a]) == (want != 0):
                    pc += off
            elif op == 'FORPREP':
                a = ins[1]
                t[a] = self.num(t[a]) - self.num(t[a + 2])
                pc += ins[2]
                continue
            elif op == 'FORLOOP':
                a, off = ins[1], ins[2]
                step = self.num(t[a + 2])
                idx = self.num(t[a]) + step
                limit = self.num(t[a + 1])
                if (step >= 0 and idx <= limit) or (step < 0 and idx >= limit):
                    t[a] = idx
                    t[a + 3] = idx
                    pc += off
            elif op == 'GENPREP':
                a, nv, off = ins[1], ins[2], ins[3]
                rets = self.call(t[a], [t[a + 1], t[a + 2]])
                for i in range(nv):
                    t[a + 3 + i] = rets[i] if i < len(rets) else None
                t[a + 2] = rets[0] if rets else None
                if t[a + 3] is None:
                    pc += off
            elif op == 'GENNEXT':
                a, nv, off = ins[1], ins[2], ins[3]
                rets = self.call(t[a], [t[a + 1], t[a + 2]])
                for i in range(nv):
                    t[a + 3 + i] = rets[i] if i < len(rets) else None
                t[a + 2] = rets[0] if rets else None
                if t[a + 3] is not None:
                    pc += off
            elif op == 'SETLIST':
                a, b, cnt, off = ins[1], ins[2], ins[3], ins[4]
                tb = t[a]
                if cnt == 255:
                    cnt = int(t[0])
                for i in range(cnt):
                    tb.set(off + i, t[b + i])
            elif op == 'CMPBR':
                a, b, enc, off = ins[1], ins[2], ins[3], ins[4]
                opd, neg = enc // 2, enc % 2
                r = self.compare(ARITH_OPS[opd - 1], t[a], res(b))
                if neg == 1:
                    r = not r
                if r:
                    pc += off
            elif op == 'HALT':
                return ()
            elif op == 'NOP':
                pass
            else:
                raise VMError('bad opcode %s' % op)

    def num(self, v):
        if isinstance(v, float):
            return v
        if isinstance(v, int):
            return float(v)
        n = self.tonum(v)
        if n is None:
            raise VMError("attempt to perform arithmetic on a %s value" % type(v).__name__)
        return n

    def arith(self, op, a, b):
        if op == '..':
            return self.tostr(a) + self.tostr(b)
        if op in ('==', '~='):
            r = self.eq(a, b)
            return r if op == '==' else (not r)
        if op == '+':
            return self.num(a) + self.num(b)
        if op == '-':
            return self.num(a) - self.num(b)
        if op == '*':
            return self.num(a) * self.num(b)
        if op == '/':
            return self.num(a) / self.num(b)
        if op == '%':
            x, y = self.num(a), self.num(b)
            return x - _math.floor(x / y) * y
        if op == '^':
            return float(self.num(a) ** self.num(b))
        if op == '//':
            return float(_math.floor(self.num(a) / self.num(b)))
        if op in ('<', '<=', '>', '>='):
            return self.compare(op, a, b)
        if op == '&':
            return float(int(self.num(a)) & int(self.num(b)))
        if op == '|':
            return float(int(self.num(a)) | int(self.num(b)))
        if op == '~':
            return float(int(self.num(a)) ^ int(self.num(b)))
        if op == '<<':
            return float((int(self.num(a)) << int(self.num(b))) & 0xffffffff)
        if op == '>>':
            return float(int(self.num(a)) >> int(self.num(b)))
        raise VMError('bad arith op %s' % op)

    def eq(self, a, b):
        if isinstance(a, Table) or isinstance(b, Table):
            return a is b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) == float(b)
        return a == b and type(a) == type(b) or a == b

    def compare(self, op, a, b):
        if isinstance(a, str) and isinstance(b, str):
            pass
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            a, b = float(a), float(b)
        elif isinstance(a, bool) is False and isinstance(b, bool) is False:
            pass
        if op == '==':
            return self.eq(a, b)
        if op == '~=':
            return not self.eq(a, b)
        if op == '<':
            return a < b
        if op == '<=':
            return a <= b
        if op == '>':
            return a > b
        if op == '>=':
            return a >= b
        raise VMError('bad compare ' + op)

    def unary(self, d, a):
        if d == 1:
            return -self.num(a)
        if d == 2:
            return not self.truth(a)
        if d == 3:
            return float(a.length() if isinstance(a, Table) else len(a))
        if d == 4:
            return float(~int(self.num(a)) & 0xffffffff)
        raise VMError('bad unary')

    def index(self, obj, key):
        if isinstance(obj, str):
            st = self.env.get('string')
            if isinstance(st, Table):
                return st.get(key)
            return None
        if isinstance(obj, Table):
            v = obj.get(key)
            if v is None and obj.mt is not None:
                idx = obj.mt.get('__index')
                if isinstance(idx, Table):
                    return idx.get(key)
                if callable(idx) or isinstance(idx, Closure):
                    r = self.call(idx, [obj, key])
                    return r[0] if r else None
            return v
        raise VMError('attempt to index a %s value' % type(obj).__name__)

    def setindex(self, obj, key, val):
        if isinstance(obj, Table):
            if obj.get(key) is None and obj.mt is not None:
                ni = obj.mt.get('__newindex')
                if isinstance(ni, Table):
                    ni.set(key, val)
                    return
                if callable(ni) or isinstance(ni, Closure):
                    self.call(ni, [obj, key, val])
                    return
            obj.set(key, val)
            return
        raise VMError('attempt to index a %s value' % type(obj).__name__)


class _K:
    __slots__ = ('i',)

    def __init__(self, i):
        self.i = i


def patch_k(protos):
    """Import K from compiler for isinstance checks."""
    from .compiler import K
    return K
