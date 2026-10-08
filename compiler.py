"""
KryptVM - AST -> custom bytecode compiler (v2).

Design
------
* One `Proto` per Lua function: instruction array + constant pool + nested protos.
* Registers live in a 64 slot table created per call.
* Instructions are tuples `(OPNAME, *operands)`; an operand is either an int
  (register / immediate) or `K(i)` (constant pool index).
* A scope-aware pre-pass resolves which names are locals, which become closure
  upvalues, and which are globals.  Upvalues are captured *by reference* to the
  defining frame's register slot, which reproduces Lua's closure semantics.
"""

from .frontend import (
    parse, unparse, Name, Num, Str, Nil, TrueE, FalseE, Vararg, Index, Call,
    MethodCall, Func, Table, TableField, BinOp, UnOp, Paren, IfExp, Interp,
    Block, LocalDecl, LocalFunc, Assign, CallStat, Do, While, Repeat, NumFor,
    GenFor, If, Return, Break, Continue, Goto, Label, TypeAlias,
)

# --------------------------------------------------------------------------
# ISA  (keep in sync with runtime.lua / vm.py)
# --------------------------------------------------------------------------
OPCODES = [
    ('NOP',      '',        '-'),
    ('MOVE',     'ww',      'R[A] = R[B]'),
    ('LOADK',    'wc',      'R[A] = K[B]'),
    ('LOADNI',   'wn',      'R[A..A+B-1] = nil'),
    ('LOADTRUE', 'w',       'R[A] = true'),
    ('LOADFALS', 'w',       'R[A] = false'),
    ('LOADI',    'wn',      'R[A] = B'),
    ('ARITH',    'wccn',    'R[A] = R[B] <op D> R[C]'),
    ('UNARY',    'wcn',     'R[A] = <unop D> R[B]'),
    ('LEN',      'wc',      'R[A] = #R[B]'),
    ('CONCAT',   'wnw',     'R[C] = R[A]..R[A+1]..?..R[A+B-1]'),
    ('NEWTAB',   'wnn',     'R[A] = {}'),
    ('GETTAB',   'wcc',     'R[A] = R[B][C]'),
    ('SETTAB',   'wcc',     '[B][C] = R[A]'),
    ('GETG',     'wc',      'R[A] = ENV[K[B]]'),
    ('SETG',     'wc',      'ENV[K[B]] = R[A]'),
    ('GETU',     'wn',      'R[A] = UV[B]'),
    ('SETU',     'wn',      'UV[B] = R[A]'),
    ('VARARG',   'wn',      'R[A..] = ...'),
    ('CALL',     'wwnw',    'R[A](...) -> R[D]'),
    ('RETURN',   'wn',      'return R[A..A+B-1]'),
    ('SELF',     'wcc',     'R[A]=R[B][C]; R[A+1]=R[B]'),
    ('CLOSURE',  'wN',      'R[A] = closure(P[B])'),
    ('ADDUP',    'wn',      'newclosure.uv += R[B]'),
    ('ADDUPU',   'wn',      'newclosure.uv += UV[B]'),
    ('JMP',      'j',       'pc += A'),
    ('TEST',     'wwj',     'if truth(R[A])==(B~=0) then pc += J'),
    ('FORPREP',  'wj',      'numeric for prep'),
    ('FORLOOP',  'wj',      'numeric for step'),
    ('GENPREP',  'wnj',     'generic for prep'),
    ('GENNEXT',  'wnj',     'generic for step'),
    ('SETLIST',  'wnnn',    'R[A][D+i-1] = R[B+i-1]'),
    ('CMPBR',    'wcnj',    'if cmp(C)(R[A],B)[^D] then pc += J'),
    ('HALT',     '',        'stop'),
    ('XORJ',     'wnj',     'obfuscation dead branch helper'),
]
OP_ID = {name: i for i, (name, _s, _d) in enumerate(OPCODES)}
OP_SIG = {name: sig for name, sig, _d in OPCODES}
OP_NAME = [n for n, _s, _d in OPCODES]
OPCOUNT = len(OPCODES)

ARITH_OPS = ['+', '-', '*', '/', '%', '^', '..', '==', '~=', '<', '<=', '>', '>=',
             '//', '&', '|', '~', '<<', '>>']
ARITH_ID = {op: i + 1 for i, op in enumerate(ARITH_OPS)}
UNARY_ID = {'-': 1, 'not': 2, '#': 3, '~': 4}
COMPARE_OPS = ('==', '~=', '<', '<=', '>', '>=')


class K:
    __slots__ = ('i',)

    def __init__(self, i):
        self.i = i

    def __repr__(self):
        return 'K(%d)' % self.i


class CompileError(Exception):
    pass


# --------------------------------------------------------------------------
# scope-aware pre-pass
# --------------------------------------------------------------------------
class Resolver:
    """Determines, for every function node, which names are captured."""

    def __init__(self):
        self.free = {}        # id(funcnode) -> {name: id(owner_funcnode)}
        self.fnodes = {}      # id(funcnode) -> funcnode

    def run(self, ast):
        self.fstack = []
        self.scopes = []

    def push_scope(self):
        self.scopes.append({})

    def pop_scope(self):
        self.scopes.pop()

    def declare(self, name):
        if self.scopes:
            self.scopes[-1][name] = id(self.fstack[-1])

    def mark_free(self, name, owner_id):
        for fn in self.fstack:
            if id(fn) != owner_id:
                self.free.setdefault(id(fn), {})
                if name not in self.free[id(fn)]:
                    self.free[id(fn)][name] = owner_id

    def use(self, name):
        for i in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[i]:
                self.mark_free(name, self.scopes[i][name])
                return
        return  # global

    # ---- walk ----------------------------------------------------------
    def block(self, node):
        self.push_scope()
        for st in node.stmts:
            self.stat(st)
        self.pop_scope()

    def stat(self, n):
        k = n.kind
        if k == 'LocalDecl':
            for e in n.exprs:
                self.expr(e)
            for nm in n.names:
                self.declare(nm)
        elif k == 'LocalFunc':
            self.declare(n.name)
            self.expr(n.func)
        elif k == 'Assign':
            for e in n.exprs:
                self.expr(e)
            for t in n.targets:
                self.expr(t)
        elif k == 'CallStat':
            self.expr(n.call)
        elif k == 'Do':
            self.block(n.body)
        elif k == 'While':
            self.expr(n.cond)
            self.block(n.body)
        elif k == 'Repeat':
            self.push_scope()
            for st in n.body.stmts:
                self.stat(st)
            self.expr(n.cond)
            self.pop_scope()
        elif k == 'NumFor':
            self.expr(n.start); self.expr(n.stop)
            if n.step: self.expr(n.step)
            self.push_scope()
            self.declare(n.var)
            for st in n.body.stmts:
                self.stat(st)
            self.pop_scope()
        elif k == 'GenFor':
            for e in n.exprs:
                self.expr(e)
            self.push_scope()
            for nm in n.names:
                self.declare(nm)
            for st in n.body.stmts:
                self.stat(st)
            self.pop_scope()
        elif k == 'If':
            for cond, body in n.clauses:
                self.expr(cond)
                self.block(body)
            if n.orelse is not None:
                self.block(n.orelse)
        elif k == 'Return':
            for e in n.exprs:
                self.expr(e)
        elif k in ('Break', 'Continue', 'Goto', 'Label', 'TypeAlias'):
            pass

    def expr(self, n):
        if n is None:
            return
        k = n.kind
        if k == 'Name':
            self.use(n.name)
        elif k in ('Num', 'Str', 'Nil', 'TrueE', 'FalseE', 'Vararg'):
            pass
        elif k == 'Index':
            self.expr(n.obj); self.expr(n.key)
        elif k == 'Call':
            self.expr(n.func)
            for a in n.args:
                self.expr(a)
        elif k == 'MethodCall':
            self.expr(n.obj)
            for a in n.args:
                self.expr(a)
        elif k == 'Func':
            self.func_node(n)
        elif k == 'Table':
            for f in n.fields:
                if f.key is not None:
                    self.expr(f.key)
                self.expr(f.value)
        elif k == 'BinOp':
            self.expr(n.l); self.expr(n.r)
        elif k == 'UnOp':
            self.expr(n.e)
        elif k == 'Paren':
            self.expr(n.e)
        elif k == 'IfExp':
            self.expr(n.c); self.expr(n.a); self.expr(n.b)
        elif k == 'Interp':
            for p in n.parts:
                if not isinstance(p, Str):
                    self.expr(p)

    def func_node(self, n):
        self.fstack.append(n)
        self.free.setdefault(id(n), {})
        self.push_scope()
        for p in n.params:
            self.declare(p)
        for st in n.body.stmts:
            self.stat(st)
        self.pop_scope()
        self.fstack.pop()


def resolve(ast):
    r = Resolver()
    r.fstack = [ast]          # the chunk itself acts as the outer function node
    r.scopes = []
    r.free.setdefault(id(ast), {})
    r.block(ast)
    r.fstack.pop()
    return r


# --------------------------------------------------------------------------
# register allocator
# --------------------------------------------------------------------------
class RegAlloc:
    def __init__(self, base=1, limit=200):
        self.next = base
        self.limit = limit
        self.pinned = set()

    def alloc(self, n=1):
        s = self.next
        while True:
            ok = s + n - 1 <= self.limit
            if ok:
                for k in range(s, s + n):
                    if k in self.pinned:
                        ok = False
                        break
            if ok:
                self.next = s + n
                return s
            s += 1
            if s + n - 1 > self.limit:
                raise CompileError('register file exhausted (split the function into smaller parts)')

    def mark(self):
        return self.next

    def release(self, m):
        pins = [p for p in self.pinned if p >= m]
        self.next = min([m] + pins)

    def pin(self, slot):
        self.pinned.add(slot)


# --------------------------------------------------------------------------
# protos / contexts
# --------------------------------------------------------------------------
class Proto:
    def __init__(self, name, nparams, vararg, index):
        self.name = name
        self.nparams = nparams
        self.vararg = vararg
        self.index = index
        self.code = []
        self.consts = []
        self.cmap = {}
        self.protos = []
        self.upvals = []          # [{'kind':'reg'|'up','ref':int,'name':str}]
        self.uindex = {}
        self.parent = None
        self.funcnode = None

    def konst(self, v):
        key = (type(v).__name__, v)
        if key in self.cmap:
            return K(self.cmap[key])
        i = len(self.consts)
        self.consts.append(v)
        self.cmap[key] = i
        return K(i)

    def emit(self, op, *args):
        self.code.append((op,) + args)
        return len(self.code) - 1

    def patch(self, pos, target):
        op = self.code[pos][0]
        args = list(self.code[pos][1:])
        j = OP_SIG[op].index('j')
        args[j] = target - (pos + 1)
        self.code[pos] = (op,) + tuple(args)

    def here(self):
        return len(self.code)


class Ctx:
    def __init__(self, proto, funcnode):
        self.proto = proto
        self.funcnode = funcnode
        self.scopes = []
        self.alloc = RegAlloc(base=max(1, proto.nparams + 1))
        self.loops = []
        self.gotos = []
        self.labels = {}

    def push(self):
        self.scopes.append({})

    def pop(self):
        sc = self.scopes.pop()
        for v in sc.values():
            if v.captured:
                self.alloc.pin(v.slot)
        self.alloc.release(self.marks.pop())

    def declare(self, name):
        slot = self.alloc.alloc()
        self.scopes[-1][name] = Var(name, slot)
        return self.scopes[-1][name]

    def lookup(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None


class Var:
    __slots__ = ('name', 'slot', 'captured')

    def __init__(self, name, slot):
        self.name = name
        self.slot = slot
        self.captured = False


# --------------------------------------------------------------------------
# Compiler
# --------------------------------------------------------------------------
class Compiler:
    def __init__(self, resolver=None):
        self.resolver = resolver
        self.ctxs = []
        self.protos = []

    # -- context helpers ---------------------------------------------------
    @property
    def c(self):
        return self.ctxs[-1]

    @property
    def p(self):
        return self.ctxs[-1].proto

    def push_scope(self):
        self.c.push()
        self.c.marks.append(self.c.alloc.mark())

    def pop_scope(self):
        self.c.pop()

    def declare(self, name):
        return self.c.declare(name)

    def lookup(self, name):
        return self.c.lookup(name)

    # -- entry -------------------------------------------------------------
    def compile(self, ast):
        main = Proto('main', 0, True, 0)
        main.funcnode = ast
        self.protos = [main]
        ctx = Ctx(main, ast)
        ctx.marks = []
        main.parent = None
        self.ctxs.append(ctx)
        ctx.push()
        ctx.marks.append(ctx.alloc.mark())
        for st in ast.stmts:
            self.stat(st)
        self.resolve_gotos()
        main.emit('HALT')
        ctx.pop()
        self.ctxs.pop()
        return main

    def new_proto(self, name, nparams, vararg, funcnode):
        pr = Proto(name, nparams, vararg, len(self.protos))
        self.protos.append(pr)
        pr.funcnode = funcnode
        pr.parent = self.p
        return pr

    # -- upvalue plumbing --------------------------------------------------
    def capture_plan(self, child, funcnode):
        """Resolve every free name of `child` to a parent reg or parent upval."""
        free = self.resolver.free.get(id(funcnode), {})
        caps = []
        for name, owner_id in free.items():
            parent = self.c
            v = None if parent.funcnode is None else parent.lookup(name)
            if v is not None and id(parent.funcnode) == owner_id:
                v.captured = True
                parent.alloc.pin(v.slot)
                caps.append({'kind': 'reg', 'ref': v.slot, 'name': name})
            else:
                idx = self.ensure_upval(name, owner_id)
                if idx is None:
                    continue
                caps.append({'kind': 'up', 'ref': idx, 'name': name})
        return caps

    def ensure_upval(self, name, owner_id):
        """Make sure the *current* proto holds `name` as an upvalue."""
        cur = self.c
        if name in cur.proto.uindex:
            return cur.proto.uindex[name]
        if cur.proto.parent is None:
            return None
        pctx = self.ctxs[-2]
        v = pctx.lookup(name) if pctx.funcnode is not None else None
        if v is not None and id(pctx.funcnode) == owner_id:
            v.captured = True
            pctx.alloc.pin(v.slot)
            cap = {'kind': 'reg', 'ref': v.slot, 'name': name}
        else:
            self.ctxs.pop()
            try:
                pidx = self.ensure_upval(name, owner_id)
            finally:
                self.ctxs.append(cur)
            if pidx is None:
                return None
            cap = {'kind': 'up', 'ref': pidx, 'name': name}
        idx = len(cur.proto.upvals)
        cur.proto.upvals.append(cap)
        cur.proto.uindex[name] = idx
        return idx

    def free_resolve(self, proto, name):
        """Index of upvalue `name` inside `proto` (must already exist)."""
        return proto.uindex.get(name)

    # -- emitting ----------------------------------------------------------
    def jmp(self, op, *args):
        """Emit a jump instruction, returns position."""
        sig = OP_SIG[op]
        padded = list(args) + [0] * (len(sig) - len(args))
        return self.p.emit(op, *padded)

    def patch(self, pos, target=None):
        if target is None:
            target = self.p.here()
        self.p.patch(pos, target)

    # -- statements --------------------------------------------------------
    def stat(self, n):
        fn = getattr(self, 'st_' + n.kind, None)
        if fn is None:
            raise CompileError('unsupported statement: ' + n.kind)
        fn(n)

    def st_TypeAlias(self, n):
        pass

    def st_LocalDecl(self, n):
        c = self.c
        m = c.alloc.mark()
        names = n.names
        k = len(names)
        base = c.alloc.alloc(max(1, k))
        got = self.exprlist(n.exprs, base, k)
        c.alloc.release(m)
        for i, nm in enumerate(names):
            v = c.declare(nm)
            if i < got or got == 255:
                if v.slot != base + i:
                    self.p.emit('MOVE', v.slot, base + i)
            elif n.exprs:
                self.p.emit('LOADNI', v.slot, 1)

    def st_LocalFunc(self, n):
        v = self.declare(n.name)
        self.closure(n.func, v.slot, n.name)

    def st_Assign(self, n):
        c = self.c
        m = c.alloc.mark()
        nt = len(n.targets)
        # 1. evaluate all RHS values into consecutive temps
        vbase = c.alloc.alloc(max(1, nt))
        got = self.exprlist(n.exprs, vbase, nt)
        # 2. resolve targets
        plans = []
        for t in n.targets:
            if isinstance(t, Name):
                plans.append(('name', t.name, None, None))
            elif isinstance(t, Index):
                o = self.eval_operand(t.obj)
                kk = self.eval_operand(t.key)
                plans.append(('idx', None, o, kk))
            else:
                raise CompileError('bad assignment target')
        # 3. store
        for i, (kind, name, o, kk) in enumerate(plans):
            if i < got:
                src = vbase + i
            elif got == 255:
                src = vbase + i
            else:
                break
            if kind == 'name':
                v = self.lookup(name)
                if v is not None:
                    if v.slot != src:
                        self.p.emit('MOVE', v.slot, src)
                else:
                    idx = self.p.uindex.get(name)
                    if idx is not None:
                        self.p.emit('SETU', src, idx)
                    else:
                        self.p.emit('SETG', src, self.p.konst(name))
            else:
                self.p.emit('SETTAB', src, o, kk)
        for kind, name, o, kk in plans:
            if kind == 'idx':
                self.free_multi(o, kk)
        c.alloc.release(m)

    def st_CallStat(self, n):
        c = self.c
        m = c.alloc.mark()
        self.compile_call(n.call, None, 0)
        c.alloc.release(m)

    def st_Do(self, n):
        self.push_scope()
        self.block(n.body)
        self.pop_scope()

    def block(self, b):
        for st in b.stmts:
            self.stat(st)

    def st_While(self, n):
        c = self.c
        start = self.p.here()
        c.loops.append(LoopCtx('while'))
        m = c.alloc.mark()
        cond = self.cond_reg(n.cond)
        j = self.jmp('TEST', cond, 0) if cond is not None else None
        c.alloc.release(m)
        self.push_scope()
        self.block(n.body)
        self.pop_scope()
        self.jmp('JMP', start - (self.p.here() + 1))
        ctx = c.loops.pop()
        end = self.p.here()
        if j is not None:
            self.patch(j, end)
        for b in ctx.breaks:
            self.patch(b, end)
        for b in ctx.continues:
            self.patch(b, start)

    def st_Repeat(self, n):
        c = self.c
        start = self.p.here()
        c.loops.append(LoopCtx('repeat'))
        self.push_scope()
        self.block(n.body)
        m = c.alloc.mark()
        cond = self.cond_reg(n.cond)
        j = self.jmp('TEST', cond, 0) if cond is not None else None
        c.alloc.release(m)
        self.pop_scope()
        ctx = c.loops.pop()
        end = self.p.here()
        if j is not None:
            self.patch(j, start)         # falsy -> loop again
            end = self.p.here()
        else:
            self.jmp('JMP', start - (self.p.here() + 1))
        for b in ctx.breaks:
            self.patch(b, end)
        for b in ctx.continues:
            self.patch(b, j if j is not None else self.p.here() - 1)

    def st_NumFor(self, n):
        c = self.c
        m = c.alloc.mark()
        base = c.alloc.alloc(4)          # [counter, limit, step, var]
        self.expr(n.start, base)
        self.expr(n.stop, base + 1)
        if n.step is not None:
            self.expr(n.step, base + 2)
        else:
            self.p.emit('LOADI', base + 2, 1)
        prep = self.jmp('FORPREP', base, 0)
        c.loops.append(LoopCtx('for'))
        self.push_scope()
        v = Var(n.var, base + 3)
        c.scopes[-1][n.var] = v
        self.block(n.body)
        self.pop_scope()
        loop = self.jmp('FORLOOP', base, 0)
        ctx = c.loops.pop()
        self.p.patch(prep, loop)
        self.p.patch(loop, prep + 1)
        end = self.p.here()
        for b in ctx.breaks:
            self.patch(b, end)
        for b in ctx.continues:
            self.patch(b, loop)
        c.alloc.release(m)

    def st_GenFor(self, n):
        c = self.c
        m = c.alloc.mark()
        nvars = len(n.names)
        base = c.alloc.alloc(3 + nvars)   # [iter, state, control, vars...]
        got = self.exprlist(n.exprs, base, 3)
        for i in range(got if got != 255 else 3, 3):
            self.p.emit('LOADNI', base + i, 1)
        prep = self.jmp('GENPREP', base, nvars, 0)
        c.loops.append(LoopCtx('genfor'))
        self.push_scope()
        for i, nm in enumerate(n.names):
            c.scopes[-1][nm] = Var(nm, base + 3 + i)
        self.block(n.body)
        self.pop_scope()
        nxt = self.jmp('GENNEXT', base, nvars, 0)
        ctx = c.loops.pop()
        self.p.patch(nxt, prep + 1)
        end = self.p.here()
        self.p.patch(prep, end)
        for b in ctx.breaks:
            self.patch(b, end)
        for b in ctx.continues:
            self.patch(b, nxt)
        c.alloc.release(m)

    def st_If(self, n):
        jends = []
        for i, (cond, body) in enumerate(n.clauses):
            m = self.c.alloc.mark()
            cv = self.cond_reg(cond)
            self.c.alloc.release(m)
            j = self.jmp('TEST', cv, 0) if cv is not None else None
            self.push_scope()
            self.block(body)
            self.pop_scope()
            has_more = (i < len(n.clauses) - 1) or (n.orelse is not None)
            if has_more:
                jends.append(self.jmp('JMP', 0))
            if j is not None:
                self.patch(j)
        if n.orelse is not None:
            self.push_scope()
            self.block(n.orelse)
            self.pop_scope()
        end = self.p.here()
        for j in jends:
            self.patch(j, end)

    def st_Return(self, n):
        c = self.c
        m = c.alloc.mark()
        if not n.exprs:
            self.p.emit('RETURN', 0, 0)
        else:
            base = c.alloc.alloc(max(1, len(n.exprs)))
            got = self.exprlist(n.exprs, base, len(n.exprs), tail_multi=True)
            self.p.emit('RETURN', base, len(n.exprs) if got != 255 else 255)
        c.alloc.release(m)

    def st_Break(self, n):
        c = self.c
        if not c.loops:
            self.p.emit('NOP')
            return
        c.loops[-1].breaks.append(self.jmp('JMP', 0))

    def st_Continue(self, n):
        c = self.c
        if not c.loops:
            self.p.emit('NOP')
            return
        c.loops[-1].continues.append(self.jmp('JMP', 0))

    def st_Goto(self, n):
        self.c.gotos.append((n.label, self.jmp('JMP', 0)))

    def st_Label(self, n):
        self.c.labels[n.name] = self.p.here()

    def resolve_gotos(self):
        for label, pos in self.c.gotos:
            tgt = self.c.labels.get(label, pos + 1)
            self.p.patch(pos, tgt)

    # -- expressions -------------------------------------------------------
    def as_reg(self, op):
        if isinstance(op, K):
            raise CompileError('expected register operand')
        return op

    def cond_reg(self, node):
        """Evaluate a condition into a register. Returns None for literal true."""
        if node.kind == 'TrueE':
            return None
        if node.kind == 'FalseE':
            r = self.c.alloc.alloc()
            self.p.emit('LOADFALS', r)
            return r
        op = self.eval_operand(node)
        if isinstance(op, K):
            r = self.c.alloc.alloc()
            self.p.emit('LOADK', r, op)
            return r
        return op

    def copy_to(self, src, dst):
        if isinstance(src, K):
            self.p.emit('LOADK', dst, src)
        elif src != dst:
            self.p.emit('MOVE', dst, src)

    def free_multi(self, *ops):
        mn = None
        for o in ops:
            if isinstance(o, int):
                mn = o if mn is None else min(mn, o)
        if mn is not None:
            self.c.alloc.release(min(self.c.alloc.mark(), mn))

    def eval_operand(self, node):
        """Register or K() for a value expression (may allocate a temp)."""
        k = node.kind
        if k == 'Num':
            return self.p.konst(node.value)
        if k == 'Str':
            return self.p.konst(node.value)
        if k == 'Nil':
            return self.p.konst(None)
        if k == 'TrueE':
            return self.p.konst(True)
        if k == 'FalseE':
            return self.p.konst(False)
        if k == 'Name':
            v = self.lookup(node.name)
            if v is not None:
                return v.slot
            idx = self.p.uindex.get(node.name)
            r = self.c.alloc.alloc()
            if idx is not None:
                self.p.emit('GETU', r, idx)
            else:
                self.p.emit('GETG', r, self.p.konst(node.name))
            return r
        r = self.c.alloc.alloc()
        self.expr(node, r)
        return r

    def load_captured(self, name):
        """Read a captured variable (upvalue) into a fresh register."""
        idx = self.free_resolve(self.p, name)
        r = self.c.alloc.alloc()
        self.p.emit('GETU', r, idx)
        return r

    def expr(self, node, target):
        k = node.kind
        if k in ('Num', 'Str', 'Nil', 'TrueE', 'FalseE'):
            val = node.value if k in ('Num', 'Str') else (
                None if k == 'Nil' else (True if k == 'TrueE' else False))
            self.p.emit('LOADK', target, self.p.konst(val))
            return
        if k == 'Name':
            v = self.lookup(node.name)
            if v is not None:
                self.p.emit('MOVE', target, v.slot)
            else:
                idx = self.p.uindex.get(node.name)
                if idx is not None:
                    self.p.emit('GETU', target, idx)
                else:
                    self.p.emit('GETG', target, self.p.konst(node.name))
            return
        if k == 'Paren':
            self.expr(node.e, target)
            return
        if k == 'Vararg':
            self.p.emit('VARARG', target, 1)
            return
        if k == 'Index':
            m = self.c.alloc.mark()
            o = self.eval_operand(node.obj)
            kk = self.eval_operand(node.key)
            self.p.emit('GETTAB', target, o, kk)
            self.c.alloc.release(m)
            return
        if k in ('Call', 'MethodCall'):
            self.compile_call(node, target, 1)
            return
        if k == 'Func':
            self.closure(node, target, 'fn')
            return
        if k == 'BinOp':
            self.binop(node, target)
            return
        if k == 'UnOp':
            self.unop(node, target)
            return
        if k == 'Table':
            self.table(node, target)
            return
        if k == 'IfExp':
            self.ifexp(node, target)
            return
        if k == 'Interp':
            self.interp(node, target)
            return
        raise CompileError('unsupported expression: ' + k)

    def const_val(self, node):
        if node.kind in ('Num', 'Str'):
            return node.value
        if node.kind == 'TrueE':
            return True
        if node.kind == 'FalseE':
            return False
        if node.kind == 'Nil':
            return None
        return _NONE

    def binop(self, node, target):
        op = node.op
        c = self.c
        lv = self.const_val(node.l)
        rv = self.const_val(node.r)
        if lv is not _NONE and rv is not _NONE and op in ('+', '-', '*', '/', '%', '^', '..'):
            val = _NONE
            try:
                if op == '+': val = lv + rv
                elif op == '-': val = lv - rv
                elif op == '*': val = lv * rv
                elif op == '/': val = lv / rv
                elif op == '%': val = lv % rv
                elif op == '^': val = lv ** rv
                elif op == '..':
                    if isinstance(lv, str) and isinstance(rv, str):
                        val = lv + rv
            except Exception:
                val = _NONE
            if val is not _NONE:
                self.p.emit('LOADK', target, self.p.konst(val))
                return
        if op in ('&', '|', '~', '<<', '>>'):
            names = {'&': 'band', '|': 'bor', '~': 'bxor', '<<': 'lshift', '>>': 'rshift'}
            m = c.alloc.mark()
            f = c.alloc.alloc()
            self.p.emit('GETG', f, self.p.konst('bit32'))
            self.p.emit('GETTAB', f, f, self.p.konst(names[op]))
            a1 = c.alloc.alloc()
            a2 = c.alloc.alloc()
            l = self.eval_operand(node.l)
            self.copy_to(l, a1)
            r = self.eval_operand(node.r)
            self.copy_to(r, a2)
            self.p.emit('CALL', f, 2, 1, target)
            c.alloc.release(m)
            return
        if op == 'and':
            self.expr(node.l, target)
            j = self.jmp('TEST', target, 0)
            m = c.alloc.mark()
            r = self.eval_operand(node.r)
            self.copy_to(r, target)
            c.alloc.release(m)
            self.patch(j)
            return
        if op == 'or':
            self.expr(node.l, target)
            j = self.jmp('TEST', target, 1)
            m = c.alloc.mark()
            r = self.eval_operand(node.r)
            self.copy_to(r, target)
            c.alloc.release(m)
            self.patch(j)
            return
        if op in COMPARE_OPS:
            m = c.alloc.mark()
            l = self.eval_operand(node.l)
            if isinstance(l, K):
                lr = c.alloc.alloc()
                self.p.emit('LOADK', lr, l)
                l = lr
            r = self.eval_operand(node.r)
            self.p.emit('CMPBR', l, r, ARITH_ID[op] * 2 + 1, 0)
            jfalse = self.p.here() - 1
            self.p.emit('LOADTRUE', target)
            jend = self.jmp('JMP', 0)
            self.patch(jfalse)
            self.p.emit('LOADFALS', target)
            self.patch(jend)
            c.alloc.release(m)
            return
        if op == '..':
            m = c.alloc.mark()
            b = c.alloc.alloc(2)
            l = self.eval_operand(node.l)
            self.copy_to(l, b)
            r = self.eval_operand(node.r)
            self.copy_to(r, b + 1)
            self.p.emit('CONCAT', b, 2, target)
            c.alloc.release(m)
            return
        m = c.alloc.mark()
        l = self.eval_operand(node.l)
        r = self.eval_operand(node.r)
        self.p.emit('ARITH', target, l, r, ARITH_ID[op])
        c.alloc.release(m)

    def unop(self, node, target):
        c = self.c
        op = node.op
        m = c.alloc.mark()
        if op == '~':
            f = c.alloc.alloc()
            self.p.emit('GETG', f, self.p.konst('bit32'))
            self.p.emit('GETTAB', f, f, self.p.konst('bnot'))
            a1 = c.alloc.alloc()
            e = self.eval_operand(node.e)
            self.copy_to(e, a1)
            self.p.emit('CALL', f, 1, 1, target)
            c.alloc.release(m)
            return
        e = self.eval_operand(node.e)
        if op == '#':
            self.p.emit('LEN', target, e)
        else:
            self.p.emit('UNARY', target, e, UNARY_ID[op])
        c.alloc.release(m)

    def interp(self, node, target):
        c = self.c
        m = c.alloc.mark()
        parts = []
        for part in node.parts:
            if isinstance(part, Str):
                parts.append(('s', self.p.konst(part.value)))
            else:
                parts.append(('e', part))
        if len(parts) == 1 and parts[0][0] == 's':
            self.p.emit('LOADK', target, parts[0][1])
            c.alloc.release(m)
            return
        base = c.alloc.alloc(len(parts))
        for i, (kind, v) in enumerate(parts):
            if kind == 's':
                self.copy_to(v, base + i)
            else:
                self.expr(v, base + i)
        self.p.emit('CONCAT', base, len(parts), target)
        c.alloc.release(m)

    def table(self, node, target):
        c = self.c
        m = c.alloc.mark()
        fields = node.fields
        arr = [f for f in fields if f.ftype == 'pos']
        has_multi = bool(arr) and arr[-1].value.kind in ('Call', 'MethodCall', 'Vararg')
        nfixed = len(arr) - 1 if has_multi else len(arr)
        self.p.emit('NEWTAB', target, min(nfixed, 255), min(len(fields) - len(arr), 255))
        if arr:
            base = c.alloc.alloc(max(1, len(arr)))
            for i in range(nfixed):
                self.expr(arr[i].value, base + i)
            if has_multi:
                last = arr[-1].value
                if last.kind == 'Vararg':
                    self.p.emit('VARARG', base + nfixed, 255)
                else:
                    self.compile_call(last, base + nfixed, 255)
                self.p.emit('SETLIST', target, base, 255, nfixed + 1)
            elif nfixed:
                self.p.emit('SETLIST', target, base, nfixed, 1)
            c.alloc.release(base)
        for f in fields:
            if f.ftype == 'pos':
                continue
            kk = self.eval_operand(f.key)
            vv = self.eval_operand(f.value)
            vr = c.alloc.alloc()
            self.copy_to(vv, vr)
            self.p.emit('SETTAB', vr, target, kk)
            self.free_multi(kk, vv)
        c.alloc.release(m)

    def ifexp(self, node, target):
        c = self.c
        m = c.alloc.mark()
        cv = self.cond_reg(node.c)
        c.alloc.release(m)
        j = self.jmp('TEST', cv, 0)
        self.expr(node.a, target)
        jend = self.jmp('JMP', 0)
        self.patch(j)
        self.expr(node.b, target)
        self.patch(jend)

    # -- closures ----------------------------------------------------------
    def closure(self, funcnode, target, name):
        child = self.new_proto(name, len(funcnode.params), funcnode.vararg, funcnode)
        caps = self.capture_plan(child, funcnode)
        child.upvals = caps
        child.uindex = {cap['name']: i for i, cap in enumerate(caps)}
        cctx = Ctx(child, funcnode)
        cctx.marks = []
        cctx.scopes.append({})
        for i, pname in enumerate(funcnode.params):
            cctx.scopes[0][pname] = Var(pname, i + 1)
        cctx.push()
        cctx.marks.append(cctx.alloc.mark())
        self.ctxs.append(cctx)
        self.block(funcnode.body)
        self.resolve_gotos()
        child.emit('RETURN', 0, 0)
        cctx.pop()
        self.ctxs.pop()
        self.p.emit('CLOSURE', target, child.index)
        for cap in caps:
            if cap['kind'] == 'reg':
                self.p.emit('ADDUP', target, cap['ref'])
            else:
                self.p.emit('ADDUPU', target, cap['ref'])

    # -- calls -------------------------------------------------------------
    def compile_call(self, node, target, nres):
        """
        target : register for results (None = discard)
        nres   : wanted results, 255 = all
        """
        c = self.c
        m = c.alloc.mark()
        is_method = node.kind == 'MethodCall'
        args = node.args
        n = len(args)
        if is_method:
            block = c.alloc.alloc(2 + n)
            fbase = block
            obj = self.eval_operand(node.obj)
            self.p.emit('SELF', fbase, obj, self.p.konst(node.name))
            self.free_multi(obj)
            got = self.exprlist(args, block + 2, n, tail_multi=True)
            nargs = (255 if got == 255 else 1 + n)
        else:
            block = c.alloc.alloc(1 + n)
            fbase = block
            self.expr(node.func, fbase)
            got = self.exprlist(args, block + 1, n, tail_multi=True)
            nargs = 255 if got == 255 else n
        dest = target if target is not None else fbase
        self.p.emit('CALL', fbase, nargs, nres, dest)
        c.alloc.release(m)

    def exprlist(self, exprs, base, want, multi_ok=True, tail_multi=False):
        """
        Evaluate `exprs` into consecutive registers from `base`.
        `want`        = slots needed (255 = unlimited)
        `tail_multi`  = the last expression expands to all of its values
        Returns slots filled, or 255 when the tail expanded to many values.
        """
        n = len(exprs)
        if n == 0:
            return 0
        for i, e in enumerate(exprs):
            last = (i == n - 1)
            slot = base + i
            if want != 255 and i >= want:
                break
            if e.kind == 'Vararg':
                if last and multi_ok and (want == 255 or tail_multi):
                    self.p.emit('VARARG', slot, 255)
                    return 255
                cnt = max(1, (want - i) if want != 255 else 1)
                self.p.emit('VARARG', slot, cnt)
                continue
            if last and e.kind in ('Call', 'MethodCall'):
                if want == 255 or (tail_multi and multi_ok):
                    self.compile_call(e, slot, 255)
                    return 255
                need = max(1, (want - i) if want != 255 else 1)
                self.compile_call(e, slot, need)
                return min(want, i + need) if want != 255 else 255
            self.expr(e, slot)
        return n

class LoopCtx:
    def __init__(self, kind):
        self.kind = kind
        self.breaks = []
        self.continues = []


_NONE = object()


def compile_ast(ast):
    res = resolve(ast)
    comp = Compiler(res)
    main = comp.compile(ast)
    return comp.protos
