"""Run a Lua file through the KryptVM compiler + reference Python VM."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from obf.frontend import parse
from obf.compiler import compile_ast, K
from obf import vm as V
from obf.vm import Table, VM, Closure

OUT = []


def lstr(v, vm):
    return vm.tostr(v)


def make_env(vm, out, stub=None):
    def lua_print(*args):
        out.append('\t'.join(vm.tostr(a) for a in args))

    def lua_type(v):
        if v is None: return 'nil'
        if isinstance(v, bool): return 'boolean'
        if isinstance(v, (int, float)): return 'number'
        if isinstance(v, str): return 'string'
        if isinstance(v, Table): return 'table'
        if isinstance(v, Closure) or callable(v): return 'function'
        return 'userdata'

    def lua_tostring(v):
        return vm.tostr(v)

    def lua_tonumber(v, base=None):
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

    def lua_next(t, k=None):
        if not isinstance(t, Table):
            return None
        keys = list(t.h.keys())
        if k is None:
            if not keys:
                return None
            k2 = keys[0]
            return (k2, t.h[k2])
        try:
            i = keys.index(k)
        except ValueError:
            return None
        if i + 1 >= len(keys):
            return None
        k2 = keys[i + 1]
        return (k2, t.h[k2])

    def lua_pairs(t):
        return (lua_next, t, None)

    def lua_ipairs(t):
        def it(tb, i=None):
            i = 1.0 if i is None else float(i) + 1
            v = tb.get(i)
            if v is None:
                return None
            return (i, v)
        return (it, t, 0.0)

    def lua_select(n, *rest):
        if n == '#':
            return float(len(rest))
        i = int(n)
        if i < 0:
            i = len(rest) + i + 1
        return tuple(rest[i - 1:])

    def lua_error(msg=None):
        raise V.VMError(vm.tostr(msg))

    def lua_pcall(fn, *args):
        try:
            r = vm.call(fn, list(args))
            return (True,) + tuple(r)
        except Exception as e:
            return (False, str(e))

    def lua_setmetatable(t, mt):
        t.mt = mt
        return t

    def lua_rawget(t, k):
        return t.h.get(k)

    def lua_rawset(t, k, v):
        t.set(k, v)
        return t

    def lua_rawequal(a, b):
        return vm.eq(a, b)

    def lua_rawlen(t):
        return float(t.length())

    def lua_ipairs_n(t):
        return float(t.length())

    def lua_collectgarbage(*a):
        return 0.0

    def lua_wait(t=None):
        return None

    def lua_assert(v=None, msg=None):
        if v is None or v is False:
            raise V.VMError(vm.tostr(msg) if msg is not None else 'assertion failed!')
        return v

    def lua_string_format(fmt, *args):
        out = []
        i = 0
        ai = 0
        while i < len(fmt):
            c = fmt[i]
            if c == '%' and i + 1 < len(fmt):
                spec = fmt[i + 1]
                if spec == '%':
                    out.append('%'); i += 2; continue
                j = i + 1
                while fmt[j] in '-+ #0123456789.':
                    j += 1
                conv = fmt[j]
                a = args[ai]; ai += 1
                if conv in 'di':
                    out.append(('%.' + '0' + 'd') % int(a))
                elif conv in 'fF':
                    out.append('%f' % float(a))
                elif conv in 'gG':
                    out.append('%g' % float(a))
                elif conv == 's':
                    out.append(vm.tostr(a))
                elif conv == 'q':
                    out.append('"%s"' % vm.tostr(a))
                elif conv == 'x':
                    out.append('%x' % int(a))
                else:
                    out.append(vm.tostr(a))
                i = j + 1
                continue
            out.append(c)
            i += 1
        return ''.join(out)

    math_t = Table(hash_={
        'floor': lambda x: float(math.floor(x)),
        'ceil': lambda x: float(math.ceil(x)),
        'abs': lambda x: abs(x),
        'max': lambda *a: max(a),
        'min': lambda *a: min(a),
        'sqrt': lambda x: math.sqrt(x),
        'huge': float('inf'),
        'pi': math.pi,
        'random': lambda *a: 0.5,
        'fmod': lambda a, b: math.fmod(a, b),
    })

    string_t = Table(hash_={
        'sub': lambda s, i, j=None: s[int(i) - 1: (int(j) if j is not None else len(s))],
        'rep': lambda s, n: s * int(n),
        'len': lambda s: float(len(s)),
        'upper': lambda s: s.upper(),
        'lower': lambda s: s.lower(),
        'format': lua_string_format,
        'byte': lambda s, i=1: float(ord(s[int(i) - 1])),
        'split': lambda s, sep: Table(arr=s.split(sep)),
        'char': lambda *a: ''.join(chr(int(x)) for x in a),
        'find': lambda s, p: (float(s.find(p) + 1) if p in s else None),
    })

    table_t = Table(hash_={
        'insert': lambda t, *a: (t.set((a[0] if len(a) > 1 else float(t.length() + 1)),
                                       a[-1]) and None),
        'concat': lambda t, sep='': sep.join(vm.tostr(t.get(float(i)))
                                             for i in range(1, int(t.length()) + 1)),
        'remove': lambda t: None,
        'unpack': lambda t: tuple(t.get(float(i)) for i in range(1, int(t.length()) + 1)),
        'sort': lambda t: None,
    })

    env = {
        'print': lua_print,
        'type': lua_type,
        'tostring': lua_tostring,
        'tonumber': lua_tonumber,
        'next': lua_next,
        'pairs': lua_pairs,
        'ipairs': lua_ipairs,
        'select': lua_select,
        'error': lua_error,
        'pcall': lua_pcall,
        'xpcall': lua_pcall,
        'setmetatable': lua_setmetatable,
        'getmetatable': lambda t: getattr(t, 'mt', None),
        'rawget': lua_rawget,
        'rawset': lua_rawset,
        'rawequal': lua_rawequal,
        'rawlen': lua_rawlen,
        'assert': lua_assert,
        'unpack': lambda t: tuple(t.get(float(i)) for i in range(1, int(t.length()) + 1)),
        'math': math_t,
        'string': string_t,
        'table': table_t,
        'os': Table(hash_={'time': lambda: 0.0, 'clock': lambda: 0.0}),
        'tick': lambda: 0.0,
        'wait': lua_wait,
        'spawn': lambda f: None,
        'task': Table(hash_={'wait': lua_wait, 'spawn': lambda f: None,
                             'defer': lambda f: None, 'delay': lambda t, f: None}),
        'bit32': Table(hash_={
            'band': lambda *a: float(__import__('functools').reduce(lambda x, y: int(x) & int(y), a)),
            'bor': lambda *a: float(__import__('functools').reduce(lambda x, y: int(x) | int(y), a)),
            'bxor': lambda *a: float(__import__('functools').reduce(lambda x, y: int(x) ^ int(y), a)),
            'bnot': lambda x: float(~int(x) & 0xffffffff),
            'lshift': lambda a, b: float((int(a) << int(b)) & 0xffffffff),
            'rshift': lambda a, b: float(int(a) >> int(b)),
        }),
        'game': Table(hash_={
            'GetService': lambda self, name: Table(hash_={'name': name,
                                                          'LocalPlayer': 'PLAYER'}),
            'Workspace': Table(),
            'Players': Table(),
        }),
    }
    if stub:
        env.update(stub)
    vm.env = env
    return env


def run_file(path, argv=None):
    src = open(path).read()
    protos = compile_ast(parse(src))
    out = []
    vm = VM({})
    make_env(vm, out)
    vm.protos = protos
    main = Closure(protos[0], None)
    main.upvals = []
    vm.call(main, [])
    return '\n'.join(out)


if __name__ == '__main__':
    print(run_file(sys.argv[1]))
