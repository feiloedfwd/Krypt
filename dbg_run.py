#!/usr/bin/env python3
"""Generate an obfuscated loader, inject a dispatch fault-catcher, run it.

    python3 tests/dbg_run.py tests/t4.lua [seed]
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from obf.frontend import parse                      # noqa: E402
from obf.compiler import compile_ast                # noqa: E402
from obf.runtime import generate                    # noqa: E402

ARGS = sys.argv[1:]
path, seed = ARGS[0], (int(ARGS[1]) if len(ARGS) > 1 else 4242)
src = generate(compile_ast(parse(open(path).read())), seed=seed)

m = re.search(r'^(\s*)pc = (\w+)\[op\]\(fr, npc, a, b, c, d\)\s*$', src, re.M)
assert m, 'dispatch line not found'
indent, F = m.group(1), m.group(2)
new = ("""%(i)slocal _ok, _r = pcall(%(F)s[op], fr, npc, a, b, c, d)
%(i)sif not _ok then
%(i)s  print('FAIL pc=' .. pc/6 .. ' op=' .. op .. ' a=' .. a .. ' b=' .. b .. ' c=' .. c .. ' d=' .. d)
%(i)s  local _p = {}
%(i)s  for _i = 1, 14 do _p[_i] = 't' .. _i .. '=' .. tostring(t[_i]) end
%(i)s  print('  ' .. table.concat(_p, ' '))
%(i)s  _p = {}
%(i)s  for _i = 1, 12 do _p[_i] = 'K' .. _i .. '=' .. tostring(fr.K[_i]) end
%(i)s  print('  ' .. table.concat(_p, ' '))
%(i)s  print('  P=' .. tostring(fr.P) .. ' np=' .. tostring(fr.P and fr.P.np))
%(i)s  error(_r)
%(i)send
%(i)spc = _r""" % {'i': indent, 'F': F})
src = src[:m.start()] + new + src[m.end():]

out = '/tmp/dbg_gen.lua'
open(out, 'w').write(src)
print('generated %s (%d chars), running...' % (out, len(src)))
r = subprocess.run(['/home/user/devtools/luau', out], capture_output=True, text=True, timeout=300)
print('--- stdout tail ---')
print('\n'.join((r.stdout or '').split('\n')[-18:]))
if r.stderr.strip():
    print('--- stderr ---')
    print('\n'.join(r.stderr.strip().split('\n')[:8]))
