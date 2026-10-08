#!/usr/bin/env python3
"""Differential check: obfuscated loader output must match plain-script output."""
import os, re, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from obf.frontend import parse
from obf.compiler import compile_ast
from obf.runtime import generate

LUAU = '/home/user/devtools/luau'

LOC = re.compile(r"[^\s']*\.lua:\d+: ")

def norm(txt):
    """error() location prefixes differ (loader vs source file); normalise them."""
    return LOC.sub('FILE: ', txt)

def run(path):
    r = subprocess.run([LUAU, path], capture_output=True, text=True, timeout=120)
    return norm(r.stdout + r.stderr), r.returncode

PRELUDE = 'tests/roblox_stub.lua'

def check(lua_path, seed=4242, raw_unicode=False, obf_level=2, jobs=1):
    src_txt = open(lua_path).read()
    pre = ''
    if open(lua_path).read().startswith('-- roblox-flavoured'):
        pre = open(PRELUDE).read()
    tmp_ref = tempfile.NamedTemporaryFile('w', suffix='.lua', delete=False)
    tmp_ref.write(pre + src_txt); tmp_ref.close()
    ref, rc0 = run(tmp_ref.name)
    os.unlink(tmp_ref.name)
    protos = compile_ast(parse(src_txt))
    for i in range(jobs):
        s = seed + i
        src = pre + generate(protos, seed=s, raw_unicode=raw_unicode, obf_level=obf_level)
        tmp = tempfile.NamedTemporaryFile('w', suffix='.lua', delete=False)
        tmp.write(src); tmp.close()
        out, rc = run(tmp.name)
        ok = out == ref
        print('%-22s seed=%-6d raw=%-5s len=%-7d %s' % (
            os.path.basename(lua_path), s, raw_unicode, len(src), 'OK' if ok else 'FAIL'))
        if not ok:
            print('   rc=%d (ref rc=%d)' % (rc, rc0))
            for a, b in zip(ref.split('\n'), out.split('\n')):
                if a != b:
                    print('   ref: %r' % a[:120]); print('   got: %r' % b[:120]); break
            os.replace(tmp.name, '/tmp/fail.lua')
            return False
        os.unlink(tmp.name)
    return True

if __name__ == '__main__':
    files = sys.argv[1:] or ['tests/t1.lua', 'tests/t2.lua']
    allok = True
    for f in files:
        for raw in (False, True):
            allok &= check(f, raw_unicode=raw, jobs=2)
    print('ALL PASS' if allok else 'FAILURES')
    sys.exit(0 if allok else 1)
