#!/usr/bin/env python3
"""Run a test script and its obfuscated twin under Roblox-like environments.

Roblox differences that matter to a VM obfuscator:
  * `getfenv` is not available to scripts (or returns nil / a locked proxy)
  * `_G` is a separate *shared* table, not the script environment
  * the script environment serves globals through __index (rawget sees nothing)

    python3 tests/env_check.py tests/t1.lua tests/t5.lua
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from obf.frontend import parse                      # noqa: E402
from obf.compiler import compile_ast                # noqa: E402
from obf.runtime import generate                    # noqa: E402

LUAU = '/home/user/devtools/luau'
LOC = re.compile(r"[^\s']*\.lua:\d+: ")
ANYPATH = re.compile(r"\S*\.lua:\d+")   # stacktrace frames: line numbers move in a one-line file

PRELUDE_STUB = 'tests/roblox_stub.lua'

STD_LIST = ('"assert","bit32","collectgarbage","coroutine","error","getmetatable",'
            '"ipairs","math","next","os","pairs","pcall","print","rawequal","rawget",'
            '"rawset","require","select","setmetatable","string","table","tonumber",'
            '"tostring","type","typeof","unpack","utf8","warn","xpcall","loadstring",'
            '"newproxy","tick","time","buffer","vector","Random","debug"')

SCENARIOS = {
    'plain': '',

    # getfenv removed entirely, _G is Roblox's separate shared table
    'no-getfenv': """
getfenv = nil
setfenv = nil
_G = {}
""",

    # getfenv exists but hands back a sandbox whose globals live behind __index
    'proxy-env': """
do
  local _std = {}
  local _orig = getfenv(1)
  for _, _k in ipairs({%s}) do _std[_k] = _orig[_k] end
  local _proxy = setmetatable({}, { __index = _std })
  getfenv = function() return _proxy end
  setfenv = nil
  _G = {}
end
""" % STD_LIST,

    # getfenv callable but useless
    'nil-env': """
getfenv = function() return nil end
setfenv = nil
_G = {}
""",

    # hostile: script environment lookup fails, only the shared table has extras
    'shared-only': """
getfenv = nil
setfenv = nil
_G = { sharedThing = "from-shared" }
""",
}


def norm(txt):
    return ANYPATH.sub('FILE:N', LOC.sub('FILE: ', txt))


def run(src_text):
    tmp = tempfile.NamedTemporaryFile('w', suffix='.lua', delete=False)
    tmp.write(src_text)
    tmp.close()
    try:
        r = subprocess.run([LUAU, tmp.name], capture_output=True, text=True, timeout=180)
        return norm(r.stdout + r.stderr), r.returncode
    finally:
        os.unlink(tmp.name)


def main(paths):
    ok_all = True
    for path in paths:
        src = open(path).read()
        stub = open(PRELUDE_STUB).read() if src.startswith('-- roblox-flavoured') else ''
        protos = compile_ast(parse(src))
        obf = generate(protos, seed=4242)
        for name, prelude in SCENARIOS.items():
            ref, rc0 = run(prelude + stub + src)
            got, rc = run(prelude + stub + obf)
            ok = (got == ref)
            ok_all &= ok
            print('%-16s %-12s %s' % (os.path.basename(path), name,
                                      'OK' if ok else 'FAIL (rc=%d/%d)' % (rc, rc0)))
            if not ok:
                for a, b in zip(ref.split('\n'), got.split('\n')):
                    if a != b:
                        print('    ref: %r' % a[:110])
                        print('    got: %r' % b[:110])
                        break
                else:
                    print('    ref first: %r' % ref.split('\n')[0][:110])
                    print('    got first: %r' % got.split('\n')[0][:110])
    print('ALL SCENARIOS PASS' if ok_all else 'FAILURES')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or ['tests/t1.lua', 'tests/t5.lua']))
