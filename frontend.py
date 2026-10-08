"""
KryptVM - Lua 5.1 / Luau frontend: lexer, AST, parser, unparser.

Written for the obfuscator: it must accept real Roblox/Luau code (type
annotations, compound assignment, continue, string interpolation,
if-then-else expressions, goto/labels, ...) and produce a clean AST.
"""

# --------------------------------------------------------------------------
# AST
# --------------------------------------------------------------------------

class Node:
    __slots__ = ()
    _fields = ()
    kind = 'Node'

    @property
    def fields(self):
        return self._fields

    def __repr__(self):
        parts = []
        for f in self.fields:
            parts.append('%s=%r' % (f, getattr(self, f, None)))
        return '%s(%s)' % (self.kind, ', '.join(parts))


def _mknode(name, fields):
    def __init__(self, *args):
        for f, a in zip(fields, args):
            setattr(self, f, a)
    cls = type(name, (Node,), {
        '__slots__': fields,
        '__init__': __init__,
        '_fields': fields,
        'kind': name,
    })
    return cls


Num        = _mknode('Num', ('value',))
Str        = _mknode('Str', ('value',))
Nil        = _mknode('Nil', ())
TrueE      = _mknode('TrueE', ())
FalseE     = _mknode('FalseE', ())
Vararg     = _mknode('Vararg', ())
Name       = _mknode('Name', ('name',))
Index      = _mknode('Index', ('obj', 'key'))
Call       = _mknode('Call', ('func', 'args'))
MethodCall = _mknode('MethodCall', ('obj', 'name', 'args'))
Func       = _mknode('Func', ('params', 'vararg', 'body'))
TableField = _mknode('TableField', ('ftype', 'key', 'value'))   # pos|kv|name|tail
Table      = _mknode('Table', ('fields',))
BinOp      = _mknode('BinOp', ('op', 'l', 'r'))
UnOp       = _mknode('UnOp', ('op', 'e'))
Paren      = _mknode('Paren', ('e',))
IfExp      = _mknode('IfExp', ('c', 'a', 'b'))
Interp     = _mknode('Interp', ('parts',))                      # Str | expr parts

# statements
Block      = _mknode('Block', ('stmts',))
LocalDecl  = _mknode('LocalDecl', ('names', 'exprs'))
LocalFunc  = _mknode('LocalFunc', ('name', 'func'))
Assign     = _mknode('Assign', ('targets', 'exprs'))
CallStat   = _mknode('CallStat', ('call',))
Do         = _mknode('Do', ('body',))
While      = _mknode('While', ('cond', 'body'))
Repeat     = _mknode('Repeat', ('body', 'cond'))
NumFor     = _mknode('NumFor', ('var', 'start', 'stop', 'step', 'body'))
GenFor     = _mknode('GenFor', ('names', 'exprs', 'body'))
If         = _mknode('If', ('clauses', 'orelse'))               # clauses=[(cond, Block)]
Return     = _mknode('Return', ('exprs',))
Break      = _mknode('Break', ())
Continue   = _mknode('Continue', ())
Goto       = _mknode('Goto', ('label',))
Label      = _mknode('Label', ('name',))
TypeAlias  = _mknode('TypeAlias', ())

EXPR_NODES = (Num, Str, Nil, TrueE, FalseE, Vararg, Name, Index, Call,
              MethodCall, Func, Table, BinOp, UnOp, Paren, IfExp, Interp)


def iter_children(node):
    """Yield child nodes of `node` (skipping plain strings / floats)."""
    if not isinstance(node, Node):
        return
    for f in node.fields:
        v = getattr(node, f, None)
        if isinstance(v, Node):
            yield v
        elif isinstance(v, list):
            for it in v:
                if isinstance(it, Node):
                    yield it
                elif isinstance(it, tuple):
                    for sub in it:
                        if isinstance(sub, Node):
                            yield sub


def walk(node):
    """Depth-first iteration of node and all descendants."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        yield n
        for c in iter_children(n):
            stack.append(c)


def mkblock(stmts):
    return Block(list(stmts))


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------

KEYWORDS = {
    'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function',
    'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then',
    'true', 'until', 'while',
}
LUAU_KEYWORDS = {'continue', 'type', 'export'}

SYMBOLS = [
    '...', '..=', '..', '::', '==', '~=', '!=', '<=', '>=', '//=', '+=', '-=',
    '*=', '/=', '%=', '^=', '//', '<<', '>>', '->', '?', '|', '&',
    '+', '-', '*', '/', '%', '^', '#', '=', '<', '>', '(', ')', '{', '}',
    '[', ']', ';', ':', ',', '.', '!', '~',
]

COMPOUND_OPS = ('+=', '-=', '*=', '/=', '%=', '^=', '..=', '//=')

ESCAPES = {
    'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
    'v': '\v', '\\': '\\', '"': '"', "'": "'", '\n': '\n',
}


class LexError(Exception):
    pass


class Tok:
    __slots__ = ('t', 'v', 'line', 'col')

    def __init__(self, t, v, line, col=0):
        self.t = t          # name | kw | num | str | intstr | sym | eof
        self.v = v
        self.line = line
        self.col = col

    def __repr__(self):
        return 'Tok(%s,%r,L%d)' % (self.t, self.v, self.line)


def _is_name_start(c):
    return c.isalpha() or c == '_' or ord(c) > 127


def _is_name_char(c):
    return c.isalnum() or c == '_' or ord(c) > 127


class Lexer:
    def __init__(self, src):
        self.src = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.toks = []

    def error(self, msg):
        raise LexError('%s at line %d' % (msg, self.line))

    def run(self):
        src, n = self.src, self.n
        while self.i < n:
            c = src[self.i]
            if c in ' \t\r':
                self.i += 1
                continue
            if c == '\n':
                self.i += 1
                self.line += 1
                continue
            if c == '-' and src.startswith('--', self.i):
                self.read_comment()
                continue
            if c == '[':
                lvl = self.long_bracket_level(self.i)
                if lvl is not None:
                    self.read_long_string(lvl)
                    continue
            if c in '"\'':
                self.read_string(c)
                continue
            if c == '`':
                self.read_interp()
                continue
            if c.isdigit() or (c == '.' and self.i + 1 < n and src[self.i + 1].isdigit()):
                self.read_number()
                continue
            if _is_name_start(c):
                self.read_name()
                continue
            self.read_symbol()
            continue
        self.toks.append(Tok('eof', None, self.line))
        return self.toks

    # -- helpers -----------------------------------------------------------
    def long_bracket_level(self, pos):
        src = self.src
        if pos >= len(src) or src[pos] != '[':
            return None
        j = pos + 1
        lvl = 0
        while j < len(src) and src[j] == '=':
            lvl += 1
            j += 1
        if j < len(src) and src[j] == '[':
            return lvl
        return None

    def read_comment(self):
        src, n = self.src, self.n
        self.i += 2
        lvl = self.long_bracket_level(self.i)
        if lvl is not None:
            self.i += lvl + 2
            closes = ']' + '=' * lvl + ']'
            end = src.find(closes, self.i)
            if end < 0:
                self.error('unfinished long comment')
            self.line += src.count('\n', self.i, end)
            self.i = end + len(closes)
            return
        while self.i < n and src[self.i] != '\n':
            self.i += 1

    def read_long_string(self, lvl):
        src, n = self.src, self.n
        start_line = self.line
        self.i += lvl + 2
        if self.i < n and src[self.i] == '\n':
            self.i += 1
            self.line += 1
        closes = ']' + '=' * lvl + ']'
        end = src.find(closes, self.i)
        if end < 0:
            self.error('unfinished long string')
        val = src[self.i:end]
        self.line += val.count('\n')
        self.i = end + len(closes)
        self.toks.append(Tok('str', val, start_line))

    def read_string(self, quote):
        src, n = self.src, self.n
        start_line = self.line
        self.i += 1
        buf = []
        while True:
            if self.i >= n:
                self.error('unfinished string')
            c = src[self.i]
            if c == quote:
                self.i += 1
                break
            if c == '\n':
                self.error('unfinished string (newline)')
            if c == '\\':
                self.i += 1
                if self.i >= n:
                    self.error('unfinished escape')
                e = src[self.i]
                if e in ESCAPES:
                    val = ESCAPES[e]
                    if val == '\n':
                        self.line += 1
                    buf.append(val)
                    self.i += 1
                elif e == 'x' or e == 'X':
                    hx = src[self.i + 1:self.i + 3]
                    if len(hx) < 2:
                        self.error('bad hex escape')
                    buf.append(chr(int(hx, 16)))
                    self.i += 3
                elif e == 'z':
                    self.i += 1
                    while self.i < n and src[self.i] in ' \t\r\n':
                        if src[self.i] == '\n':
                            self.line += 1
                        self.i += 1
                elif e == 'u':
                    self.i += 1
                    if self.i < n and src[self.i] == '{':
                        j = src.find('}', self.i)
                        if j < 0:
                            self.error('bad unicode escape')
                        cp = int(src[self.i + 1:j], 16)
                        buf.append(chr(cp))
                        self.i = j + 1
                    else:
                        self.error('bad unicode escape')
                elif e.isdigit():
                    num = ''
                    k = 0
                    while k < 3 and self.i < n and src[self.i].isdigit():
                        num += src[self.i]
                        self.i += 1
                        k += 1
                    v = int(num)
                    if v > 255:
                        self.error('decimal escape too large')
                    buf.append(chr(v))
                else:
                    buf.append(e)
                    self.i += 1
                continue
            buf.append(c)
            self.i += 1
        self.toks.append(Tok('str', ''.join(buf), start_line))

    def read_interp(self):
        """Luau `hello {name}!` string interpolation."""
        src, n = self.src, self.n
        start_line = self.line
        self.i += 1
        parts = []
        buf = []
        while True:
            if self.i >= n:
                self.error('unfinished interpolated string')
            c = src[self.i]
            if c == '`':
                self.i += 1
                break
            if c == '\\':
                nxt = src[self.i + 1] if self.i + 1 < n else ''
                if nxt in ('`', '{', '}'):
                    buf.append(nxt)
                    self.i += 2
                elif nxt in ESCAPES:
                    buf.append(ESCAPES[nxt])
                    if ESCAPES[nxt] == '\n':
                        self.line += 1
                    self.i += 2
                elif nxt == 'n':
                    buf.append('\n')
                    self.i += 2
                else:
                    buf.append(nxt)
                    self.i += 2
                continue
            if c == '\n':
                self.line += 1
                buf.append(c)
                self.i += 1
                continue
            if c == '{':
                if parts or buf:
                    parts.append(('s', ''.join(buf)))
                buf = []
                self.i += 1
                depth = 1
                estart = self.i
                while self.i < n:
                    ch = src[self.i]
                    if ch in '"\'':
                        q = ch
                        self.i += 1
                        while self.i < n and src[self.i] != q:
                            if src[self.i] == '\\':
                                self.i += 1
                            if src[self.i] == '\n':
                                self.line += 1
                            self.i += 1
                        self.i += 1
                        continue
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            break
                    elif ch == '\n':
                        self.line += 1
                    self.i += 1
                expr_src = src[estart:self.i]
                parts.append(('e', expr_src))
                self.i += 1
                continue
            buf.append(c)
            self.i += 1
        parts.append(('s', ''.join(buf)))
        self.toks.append(Tok('intstr', parts, start_line))

    def read_number(self):
        src, n = self.src, self.n
        start = self.i
        start_line = self.line
        if src.startswith('0x', self.i) or src.startswith('0X', self.i):
            self.i += 2
            while self.i < n and (src[self.i].isalnum() or src[self.i] in '._'):
                self.i += 1
            raw = src[start:self.i]
            try:
                v = float(int(raw[2:].replace('_', ''), 16))
            except ValueError:
                if '.' in raw or 'p' in raw.lower():
                    try:
                        v = float.fromhex(raw.replace('_', ''))
                    except ValueError:
                        raise LexError('malformed number %r' % raw)
                else:
                    raise LexError('malformed number %r' % raw)
        elif src.startswith('0b', self.i) or src.startswith('0B', self.i):
            self.i += 2
            while self.i < n and (src[self.i] in '01_'):
                self.i += 1
            raw = src[start:self.i]
            v = float(int(raw[2:].replace('_', ''), 2))
        else:
            while self.i < n and (src[self.i].isdigit() or src[self.i] == '_'):
                self.i += 1
            if self.i < n and src[self.i] == '.' and not src.startswith('..', self.i):
                self.i += 1
                while self.i < n and (src[self.i].isdigit() or src[self.i] == '_'):
                    self.i += 1
            if self.i < n and src[self.i] in 'eE':
                j = self.i + 1
                if j < n and src[j] in '+-':
                    j += 1
                if j < n and src[j].isdigit():
                    self.i = j
                    while self.i < n and src[self.i].isdigit():
                        self.i += 1
            raw = src[start:self.i]
            try:
                v = float(raw.replace('_', ''))
            except ValueError:
                raise LexError('malformed number %r' % raw)
        self.toks.append(Tok('num', (v, raw), start_line))

    def read_name(self):
        src, n = self.src, self.n
        start = self.i
        while self.i < n and _is_name_char(src[self.i]):
            self.i += 1
        name = src[start:self.i]
        if name in KEYWORDS:
            self.toks.append(Tok('kw', name, self.line))
        else:
            self.toks.append(Tok('name', name, self.line))

    def read_symbol(self):
        src = self.src
        for s in SYMBOLS:
            if src.startswith(s, self.i):
                self.toks.append(Tok('sym', s, self.line))
                self.i += len(s)
                return
        raise LexError('unexpected character %r at line %d' % (src[self.i], self.line))


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

BIN_PRI = {
    'or': (1, 1), 'and': (2, 2),
    '<': (3, 3), '>': (3, 3), '<=': (3, 3), '>=': (3, 3),
    '~=': (3, 3), '==': (3, 3), '!=': (3, 3),
    '|': (4, 4),            # Luau type-union only appears in types (skipped)
    '~': (5, 5),
    '&': (6, 6),
    '<<': (7, 7), '>>': (7, 7),
    '..': (9, 8),           # right assoc
    '+': (10, 10), '-': (10, 10),
    '*': (11, 11), '/': (11, 11), '//': (11, 11), '%': (11, 11),
    '^': (14, 13),          # right assoc, binds tighter than unary
}
UNARY_PRI = 12


class ParseError(Exception):
    pass


class Parser:
    def __init__(self, toks, src=None):
        self.toks = toks
        self.src = src
        self.p = 0
        self.depth = 0

    # -- token helpers -----------------------------------------------------
    def peek(self, k=0):
        i = self.p + k
        if i >= len(self.toks):
            return self.toks[-1]
        return self.toks[i]

    def next(self):
        t = self.toks[self.p]
        if t.t != 'eof':
            self.p += 1
        return t

    def at(self, t, v=None):
        tk = self.peek()
        if tk.t != t:
            return False
        return v is None or tk.v == v

    def at_sym(self, v):
        return self.at('sym', v)

    def at_kw(self, v):
        return self.at('kw', v)

    def accept(self, t, v=None):
        if self.at(t, v):
            return self.next()
        return None

    def expect(self, t, v=None):
        tk = self.peek()
        if tk.t != t or (v is not None and tk.v != v):
            self.error('expected %s %r but got %s %r' % (t, v, tk.t, tk.v))
        return self.next()

    def error(self, msg):
        tk = self.peek()
        raise ParseError('line %d: %s' % (tk.line, msg))

    # -- type annotation skipping (Luau) -----------------------------------
    def skip_type(self, stop_syms=()):
        if self.at_sym('?'):
            self.next()
            return
        self.skip_type_primary()
        while self.at_sym('|') or self.at_sym('&'):
            self.next()
            self.skip_type_primary()
        if self.at_sym('?'):
            self.next()

    def skip_type_primary(self):
        if self.at_sym('('):
            self.skip_balanced('(', ')')
        elif self.at_sym('{'):
            self.skip_balanced('{', '}')
        elif self.at_sym('<'):
            self.skip_balanced_angles()
        elif self.at_sym('...'):
            self.next()
        elif self.at('name') and self.peek().v == 'typeof' and self.peek(1).t == 'sym' \
                and self.peek(1).v == '(':
            self.next()
            self.skip_balanced('(', ')')
        elif self.at('name'):
            self.next()
            while self.at_sym('.'):
                self.next()
                self.expect('name')
            if self.at_sym('<'):
                self.skip_balanced_angles()
            if self.at_sym('('):
                self.skip_balanced('(', ')')
        elif self.at('str') or self.at('num'):
            self.next()
        elif self.at_kw('nil') or self.at_kw('true') or self.at_kw('false'):
            self.next()
        else:
            self.error('bad type annotation near %r' % (self.peek().v,))
        while self.at_sym('?'):
            self.next()
        if self.at_sym('->'):
            self.next()
            self.skip_type()

    def skip_balanced(self, o, c):
        self.expect('sym', o)
        depth = 1
        while depth:
            tk = self.next()
            if tk.t == 'eof':
                self.error('unbalanced %s' % o)
            if tk.t == 'sym' and tk.v == o:
                depth += 1
            elif tk.t == 'sym' and tk.v == c:
                depth -= 1

    def skip_balanced_angles(self):
        self.expect('sym', '<')
        depth = 1
        while depth:
            tk = self.next()
            if tk.t == 'eof':
                self.error('unbalanced <')
            if tk.t == 'sym' and tk.v == '<':
                depth += 1
            elif tk.t == 'sym' and tk.v == '>':
                depth -= 1
            elif tk.t == 'sym' and tk.v == '>>':
                depth -= 2
            elif tk.t == 'sym' and tk.v == '>=':
                depth -= 1

    # -- entry -------------------------------------------------------------
    def parse_chunk(self):
        blk = self.parse_block()
        if not self.at('eof'):
            self.error('unexpected %r after chunk' % (self.peek().v,))
        return blk

    BLOCK_END = {'end', 'else', 'elseif', 'until'}

    def parse_block(self):
        stmts = []
        while True:
            tk = self.peek()
            if tk.t == 'eof':
                break
            if tk.t == 'kw' and tk.v in self.BLOCK_END:
                break
            st = self.parse_statement()
            if st is not None:
                if isinstance(st, list):
                    stmts.extend(st)
                else:
                    stmts.append(st)
            if stmts and isinstance(stmts[-1], Return):
                break
        # consume trailing 'return' handled inside parse_statement
        return Block(stmts)

    def parse_statement(self):
        tk = self.peek()
        if tk.t == 'sym' and tk.v == ';':
            self.next()
            return None
        if tk.t == 'kw':
            v = tk.v
            if v == 'local':
                return self.parse_local()
            if v == 'if':
                return self.parse_if()
            if v == 'while':
                self.next()
                cond = self.parse_expr()
                self.expect('kw', 'do')
                body = self.parse_block()
                self.expect('kw', 'end')
                return While(cond, body)
            if v == 'do':
                self.next()
                body = self.parse_block()
                self.expect('kw', 'end')
                return Do(body)
            if v == 'for':
                return self.parse_for()
            if v == 'repeat':
                self.next()
                body = self.parse_block()
                self.expect('kw', 'until')
                cond = self.parse_expr()
                return Repeat(body, cond)
            if v == 'function':
                return self.parse_function_stat()
            if v == 'return':
                self.next()
                exprs = []
                if not (self.at('eof') or self.at_sym(';') or
                        (self.peek().t == 'kw' and self.peek().v in self.BLOCK_END)):
                    exprs = self.parse_exprlist()
                self.accept('sym', ';')
                return Return(exprs)
            if v == 'break':
                self.next()
                return Break()
            if v == 'goto':
                self.next()
                name = self.expect('name').v
                return Goto(name)
        if tk.t == 'name':
            v = tk.v
            nxt = self.peek(1)
            nxt2 = self.peek(2)
            # Luau contextual keywords
            if v == 'type' and nxt.t == 'name':
                self.skip_type_statement()
                return None
            if v == 'export' and nxt.t == 'name' and nxt.v == 'type' and nxt2.t == 'name':
                self.next()
                self.skip_type_statement()
                return None
            if v == 'goto' and nxt.t == 'name':
                self.next()
                return Goto(self.expect('name').v)
            if v == 'continue' and not self.continue_is_expr():
                self.next()
                return Continue()
        if tk.t == 'sym' and tk.v == '::':
            self.next()
            name = self.expect('name').v
            self.expect('sym', '::')
            return Label(name)
        return self.parse_exprstat()

    def continue_is_expr(self):
        """True when the `continue` token is really an identifier in an expression."""
        tk = self.peek(1)
        if tk.t == 'sym' and tk.v in ('(', '{', '.', '[', ':', '=', ',', ';'):
            return True
        if tk.t == 'str':
            return True
        return False

    def skip_type_statement(self):
        """`type X = ...` / `export type X = ...` -> dropped (types are erased)."""
        if self.at('name') and self.peek().v == 'type':
            self.next()  # 'type'
        if self.at_sym('<'):
            self.skip_balanced_angles()
        if self.at('name'):
            self.next()
        while self.at_sym('.'):
            self.next()
            self.expect('name')
        if self.at_sym('<'):
            self.skip_balanced_angles()
        if self.at_sym('='):
            self.next()
            try:
                self.skip_type()
            except ParseError:
                # heuristic recovery: skip to end of line
                while not self.at('eof') and self.peek().line == self.peek().line:
                    self.next()
        return TypeAlias()

    def parse_local(self):
        self.next()  # local
        if self.at_kw('function'):
            self.next()
            name = self.expect('name').v
            if self.at_sym('<'):
                self.skip_balanced_angles()
            func = self.parse_funcbody(attrname=name)
            return LocalFunc(name, func)
        names = []
        while True:
            nm = self.expect('name').v
            if self.at_sym(':'):
                self.next()
                self.skip_type()
            names.append(nm)
            if not self.accept('sym', ','):
                break
        exprs = []
        if self.accept('sym', '='):
            exprs = self.parse_exprlist()
        return LocalDecl(names, exprs)

    def parse_if(self):
        self.next()  # if
        clauses = []
        cond = self.parse_expr()
        self.expect('kw', 'then')
        body = self.parse_block()
        clauses.append((cond, body))
        orelse = None
        while True:
            if self.at_kw('elseif'):
                self.next()
                c = self.parse_expr()
                self.expect('kw', 'then')
                b = self.parse_block()
                clauses.append((c, b))
                continue
            if self.at_kw('else'):
                self.next()
                orelse = self.parse_block()
            break
        self.expect('kw', 'end')
        return If(clauses, orelse)

    def parse_for(self):
        self.next()  # for
        first = self.expect('name').v
        if self.at_sym(':'):
            self.next()
            self.skip_type()
        if self.at_sym('='):
            self.next()
            start = self.parse_expr()
            self.expect('sym', ',')
            stop = self.parse_expr()
            step = None
            if self.accept('sym', ','):
                step = self.parse_expr()
            self.expect('kw', 'do')
            body = self.parse_block()
            self.expect('kw', 'end')
            return NumFor(first, start, stop, step, body)
        names = [first]
        while self.accept('sym', ','):
            nm = self.expect('name').v
            if self.at_sym(':'):
                self.next()
                self.skip_type()
            names.append(nm)
        self.expect('kw', 'in')
        exprs = self.parse_exprlist()
        self.expect('kw', 'do')
        body = self.parse_block()
        self.expect('kw', 'end')
        return GenFor(names, exprs, body)

    def parse_function_stat(self):
        self.next()  # function
        tk = self.expect('name')
        target = Name(tk.v)
        is_method = False
        while self.at_sym('.'):
            self.next()
            key = self.expect('name').v
            target = Index(target, Str(key))
        if self.at_sym(':'):
            self.next()
            key = self.expect('name').v
            target = Index(target, Str(key))
            is_method = True
        if self.at_sym('<'):
            self.skip_balanced_angles()
        func = self.parse_funcbody(attrname=tk.v, is_method=is_method)
        return Assign([target], [func])

    def parse_funcbody(self, attrname=None, is_method=False):
        self.expect('sym', '(')
        params = []
        vararg = False
        if is_method:
            params.append('self')
        if not self.at_sym(')'):
            while True:
                if self.at_sym('...'):
                    self.next()
                    if self.at_sym(':'):
                        self.next()
                        self.skip_type()
                    vararg = True
                    break
                nm = self.expect('name').v
                if self.at_sym(':'):
                    self.next()
                    self.skip_type()
                params.append(nm)
                if not self.accept('sym', ','):
                    break
        self.expect('sym', ')')
        if self.at_sym(':'):
            self.next()
            self.skip_type()
        body = self.parse_block()
        self.expect('kw', 'end')
        return Func(params, vararg, body)

    def parse_exprstat(self):
        start = self.p
        e = self.parse_suffixed()
        # assignment?
        if self.at_sym('=') or self.at_sym(',') or self.is_compound_assign():
            targets = [e]
            while self.accept('sym', ','):
                targets.append(self.parse_suffixed())
            for t in targets:
                if not isinstance(t, (Name, Index)):
                    self.error('cannot assign to this expression')
            # compound assignment (only single target)
            tk = self.peek()
            if tk.t == 'sym' and tk.v in COMPOUND_OPS:
                op = tk.v[:-1]
                self.next()
                rhs = self.parse_expr()
                if len(targets) != 1:
                    self.error('compound assignment needs a single target')
                return Assign(targets, [BinOp(op, targets[0], rhs)])
            self.expect('sym', '=')
            exprs = self.parse_exprlist()
            return Assign(targets, exprs)
        # statement must be a call
        if isinstance(e, Call) or isinstance(e, MethodCall):
            return CallStat(e)
        self.p = start
        # tolerate stray literals / leftovers (some Roblox scripts have odd tails)
        self.error('unexpected expression statement')

    def is_compound_assign(self):
        tk = self.peek()
        return tk.t == 'sym' and tk.v in COMPOUND_OPS

    def parse_exprlist(self):
        exprs = [self.parse_expr()]
        while self.accept('sym', ','):
            if self.at('eof') or (self.peek().t == 'kw' and self.peek().v in self.BLOCK_END):
                break
            exprs.append(self.parse_expr())
        return exprs

    # -- expressions -------------------------------------------------------
    def parse_expr(self, limit=0):
        if self.at_kw('if'):
            return self.parse_ifexp()
        tk = self.peek()
        if (tk.t == 'kw' and tk.v == 'not') or (tk.t == 'sym' and tk.v in ('-', '#', '!', '~')):
            op = tk.v
            self.next()
            operand = self.parse_expr(UNARY_PRI)
            left = UnOp('not' if op == '!' else op, operand)
        else:
            left = self.parse_simple()
        while True:
            tk = self.peek()
            op = None
            if tk.t == 'kw' and tk.v in ('and', 'or'):
                op = tk.v
            elif tk.t == 'sym' and tk.v in BIN_PRI:
                op = tk.v
            if op is None:
                break
            lpri, rpri = BIN_PRI[op]
            if lpri <= limit:
                break
            self.next()
            right = self.parse_expr(rpri)
            left = BinOp('~=' if op == '!=' else op, left, right)
        return left

    def parse_ifexp(self):
        self.expect('kw', 'if')
        c = self.parse_expr()
        self.expect('kw', 'then')
        a = self.parse_expr()
        self.expect('kw', 'else')
        b = self.parse_expr()
        return IfExp(c, a, b)

    def parse_simple(self):
        tk = self.peek()
        if tk.t == 'num':
            self.next()
            return Num(tk.v[0])
        if tk.t == 'str':
            self.next()
            return Str(tk.v)
        if tk.t == 'intstr':
            self.next()
            parts = []
            for kind, val in tk.v:
                if kind == 's':
                    if val != '':
                        parts.append(Str(val))
                else:
                    sub = Parser(Lexer(val).run(), val)
                    parts.append(sub.parse_expr())
            if not parts:
                parts = [Str('')]
            return Interp(parts)
        if tk.t == 'kw':
            if tk.v == 'nil':
                self.next()
                return Nil()
            if tk.v == 'true':
                self.next()
                return TrueE()
            if tk.v == 'false':
                self.next()
                return FalseE()
            if tk.v == 'function':
                self.next()
                if self.at_sym('<'):
                    self.skip_balanced_angles()
                return self.parse_funcbody()
            self.error('unexpected keyword %r' % tk.v)
        if tk.t == 'sym':
            if tk.v == '...':
                self.next()
                return Vararg()
            if tk.v == '{':
                return self.parse_table()
        return self.parse_suffixed()

    def parse_primary(self):
        tk = self.peek()
        if tk.t == 'name':
            self.next()
            return Name(tk.v)
        if tk.t == 'sym' and tk.v == '(':
            self.next()
            e = self.parse_expr()
            self.expect('sym', ')')
            return Paren(e)
        self.error('unexpected token %r' % (tk.v,))

    def parse_suffixed(self):
        e = self.parse_primary()
        while True:
            tk = self.peek()
            if tk.t == 'sym':
                if tk.v == '.':
                    self.next()
                    key = self.expect('name').v
                    e = Index(e, Str(key))
                    continue
                if tk.v == '[':
                    self.next()
                    k = self.parse_expr()
                    self.expect('sym', ']')
                    e = Index(e, k)
                    continue
                if tk.v == ':':
                    self.next()
                    nm = self.expect('name').v
                    args = self.parse_args()
                    e = MethodCall(e, nm, args)
                    continue
                if tk.v == '(' or tk.v == '{' or tk.v == 'str':
                    args = self.parse_args()
                    e = Call(e, args)
                    continue
                if tk.v == '::':
                    self.next()
                    self.skip_type()
                    continue
            break
        return e

    def parse_args(self):
        tk = self.peek()
        if tk.t == 'str':
            self.next()
            return [Str(tk.v)]
        if tk.t == 'sym' and tk.v == '{':
            return [self.parse_table()]
        self.expect('sym', '(')
        args = []
        if not self.at_sym(')'):
            args = self.parse_exprlist()
        self.expect('sym', ')')
        return args

    def parse_table(self):
        self.expect('sym', '{')
        fields = []
        while not self.at_sym('}'):
            tk = self.peek()
            if tk.t == 'sym' and tk.v == '[':
                self.next()
                k = self.parse_expr()
                self.expect('sym', ']')
                self.expect('sym', '=')
                v = self.parse_expr()
                fields.append(TableField('kv', k, v))
            elif tk.t == 'name' and self.peek(1).t == 'sym' and self.peek(1).v == '=':
                self.next()
                self.next()
                v = self.parse_expr()
                fields.append(TableField('name', Str(tk.v), v))
            else:
                v = self.parse_expr()
                fields.append(TableField('pos', None, v))
            if self.at_sym(',') or self.at_sym(';'):
                self.next()
                continue
            break
        self.expect('sym', '}')
        return Table(fields)


def parse(src):
    lex = Lexer(src)
    toks = lex.run()
    p = Parser(toks, src)
    return p.parse_chunk()


# --------------------------------------------------------------------------
# Unparser (AST -> Lua source)
# --------------------------------------------------------------------------

LUA_KEYWORDS = KEYWORDS | {'goto'}

_STR_ESC = {
    '\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t',
    '\a': '\\a', '\b': '\\b', '\f': '\\f', '\v': '\\v',
}


def quote_string(s):
    out = ['"']
    for ch in s:
        if ch in _STR_ESC:
            out.append(_STR_ESC[ch])
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append('\\%d' % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def format_number(v):
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    if v != v:
        return '(0/0)'
    if v == float('inf'):
        return 'math.huge'
    if v == float('-inf'):
        return '-math.huge'
    r = repr(v)
    return r


def is_name_safe(n):
    if not n:
        return False
    if n in LUA_KEYWORDS:
        return False
    if not _is_name_start(n[0]):
        return False
    return all(_is_name_char(c) for c in n)


class Unparser:
    """Emit Lua source from the AST.  `renamer` maps Name->new name."""

    def __init__(self, indent='  ', renamer=None):
        self.indent = indent
        self.renamer = renamer

    def block(self, node, lvl=0):
        out = []
        for st in node.stmts:
            s = self.stat(st, lvl)
            if s:
                out.append(s)
        return out

    def stats(self, node, lvl):
        return '\n'.join(self.block(node, lvl))

    def ind(self, lvl):
        return self.indent * lvl

    def stat(self, n, lvl):
        i = self.ind(lvl)
        k = n.kind
        if k == 'LocalDecl':
            names = ', '.join(self.var(n2, True) for n2 in n.names)
            if n.exprs:
                return '%slocal %s = %s' % (i, names, self.exprlist(n.exprs, lvl))
            return '%slocal %s' % (i, names)
        if k == 'LocalFunc':
            return '%slocal function %s%s' % (i, self.var(n.name, True), self.funcpart(n.func, lvl))
        if k == 'Assign':
            tg = ', '.join(self.expr(t, lvl) for t in n.targets)
            return '%s%s = %s' % (i, tg, self.exprlist(n.exprs, lvl))
        if k == 'CallStat':
            return '%s%s' % (i, self.expr(n.call, lvl))
        if k == 'Do':
            return '%sdo\n%s\n%send' % (i, self.stats(n.body, lvl + 1), i)
        if k == 'While':
            return '%swhile %s do\n%s\n%send' % (i, self.expr(n.cond, lvl), self.stats(n.body, lvl + 1), i)
        if k == 'Repeat':
            return '%srepeat\n%s\n%suntil %s' % (i, self.stats(n.body, lvl + 1), i, self.expr(n.cond, lvl))
        if k == 'NumFor':
            head = 'for %s = %s, %s' % (self.var(n.var, True), self.expr(n.start, lvl), self.expr(n.stop, lvl))
            if n.step is not None:
                head += ', %s' % self.expr(n.step, lvl)
            return '%s%s do\n%s\n%send' % (i, head, self.stats(n.body, lvl + 1), i)
        if k == 'GenFor':
            return '%sfor %s in %s do\n%s\n%send' % (
                i, ', '.join(self.var(v, True) for v in n.names),
                self.exprlist(n.exprs, lvl), self.stats(n.body, lvl + 1), i)
        if k == 'If':
            parts = []
            for idx, (cond, body) in enumerate(n.clauses):
                kw = 'if' if idx == 0 else 'elseif'
                parts.append('%s%s %s then\n%s' % (i, kw, self.expr(cond, lvl), self.stats(body, lvl + 1)))
            if n.orelse is not None:
                parts.append('%selse\n%s' % (i, self.stats(n.orelse, lvl + 1)))
            parts.append('%send' % i)
            return '\n'.join(parts)
        if k == 'Return':
            if n.exprs:
                return '%sreturn %s' % (i, self.exprlist(n.exprs, lvl))
            return '%sreturn' % i
        if k == 'Break':
            return '%sbreak' % i
        if k == 'Continue':
            return '%scontinue' % i
        if k == 'Goto':
            return '%sgoto %s' % (i, n.label)
        if k == 'Label':
            return '%s::%s::' % (i, n.name)
        if k == 'TypeAlias':
            return ''
        raise ParseError('cannot unparse statement %s' % k)

    def var(self, name, declare=False):
        if self.renamer:
            return self.renamer(name)
        return name

    def exprlist(self, exprs, lvl):
        return ', '.join(self.expr(e, lvl) for e in exprs)

    def funcpart(self, f, lvl):
        params = ', '.join([self.var(p, True) for p in f.params])
        if f.vararg:
            params = params + ', ...' if params else '...'
        inner = self.stats(f.body, lvl + 1)
        if inner:
            return '(%s)\n%s\n%send' % (params, inner, self.ind(lvl))
        return '(%s)\n%send' % (params, self.ind(lvl))

    def expr(self, n, lvl=0):
        k = n.kind
        if k == 'Num':
            return format_number(n.value)
        if k == 'Str':
            return quote_string(n.value)
        if k == 'Nil':
            return 'nil'
        if k == 'TrueE':
            return 'true'
        if k == 'FalseE':
            return 'false'
        if k == 'Vararg':
            return '...'
        if k == 'Name':
            return self.var(n.name)
        if k == 'Index':
            if n.key.kind == 'Str' and is_name_safe(n.key.value):
                return '%s.%s' % (self.expr(n.obj, lvl), n.key.value)
            return '%s[%s]' % (self.expr(n.obj, lvl), self.expr(n.key, lvl))
        if k == 'Call':
            return '%s(%s)' % (self.expr(n.func, lvl), self.exprlist(n.args, lvl))
        if k == 'MethodCall':
            return '%s:%s(%s)' % (self.expr(n.obj, lvl), n.name, self.exprlist(n.args, lvl))
        if k == 'Func':
            return 'function%s' % self.funcpart(n, lvl)
        if k == 'Table':
            if not n.fields:
                return '{}'
            items = []
            for f in n.fields:
                if f.ftype == 'pos':
                    items.append(self.expr(f.value, lvl))
                elif f.ftype == 'name':
                    if is_name_safe(f.key.value):
                        items.append('%s = %s' % (f.key.value, self.expr(f.value, lvl)))
                    else:
                        items.append('[%s] = %s' % (self.expr(f.key, lvl), self.expr(f.value, lvl)))
                else:
                    items.append('[%s] = %s' % (self.expr(f.key, lvl), self.expr(f.value, lvl)))
            return '{%s}' % ', '.join(items)
        if k == 'BinOp':
            return '(%s %s %s)' % (self.expr(n.l, lvl), n.op, self.expr(n.r, lvl))
        if k == 'UnOp':
            sp = ' ' if n.op == 'not' else ''
            return '(%s%s%s)' % (n.op, sp, self.expr(n.e, lvl))
        if k == 'Paren':
            return '(%s)' % self.expr(n.e, lvl)
        if k == 'IfExp':
            return '(if %s then %s else %s)' % (self.expr(n.c, lvl), self.expr(n.a, lvl), self.expr(n.b, lvl))
        if k == 'Interp':
            return ' .. '.join('tostring(%s)' % self.expr(p, lvl) if not isinstance(p, Str)
                               else self.expr(p, lvl) for p in n.parts)
        raise ParseError('cannot unparse expression %s' % k)


def unparse(node):
    return Unparser().stats(node, 0) if isinstance(node, Block) else Unparser().expr(node)
