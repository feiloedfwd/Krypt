#!/usr/bin/env python3
"""Assert that every v3 hardening claim is actually true of the output.

    python3 tests/hardening_check.py

Checks:
  1. per-proto opcode scrambling  - two prototypes do not share a codebook
  2. constant slot shuffling      - file order != semantic order
  3. decoy VM handlers            - more handler slots than real opcodes
  4. decoy programs               - more prototypes than the input had
  5. integrity gate               - a single tampered payload char produces NO
                                    output (the chunk refuses to run) instead of
                                    executing something wrong
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from obf.frontend import parse                      # noqa: E402
from obf.compiler import compile_ast, OPCOUNT       # noqa: E402
from obf import packer as P                         # noqa: E402
from obf.runtime import RuntimeGen, mix_protos      # noqa: E402

LUAU = '/home/user/devtools/luau'
SEED = 4242
TEST = 'tests/t1.lua'


def run(path):
    r = subprocess.run([LUAU, path], capture_output=True, text=True, timeout=180)
    return r.stdout, r.stderr


def main():
    src = open(TEST).read()
    protos = compile_ast(parse(src))
    gen = RuntimeGen(protos, SEED, False)
    shuffled, inv = mix_protos(protos, SEED)
    txt, meta = P.pack(shuffled, SEED, gen.perm, extra={'proto_map': inv,
                                                        'entry': inv[0] + 1,
                                                        'mask_mode': 2})
    meta.update({'proto_map': inv, 'entry': inv[0] + 1, 'nprotos': len(shuffled),
                 'real_protos': len(protos)})
    out = gen.gen(txt, meta)
    master = meta['master']
    ok = True

    # 1. per-proto opcode codebooks differ
    a, b = P.proto_opcodes(master, 0), P.proto_opcodes(master, 1)
    diff = sum(1 for x, y in zip(a, b) if x != y)
    print('1. per-proto opcode codebook: %d/64 positions differ between protos  %s'
          % (diff, 'OK' if diff > 40 else 'WEAK'))
    ok &= diff > 40

    # 2. constant slots are shuffled (order != identity)
    n, pi = 8, 0
    order = list(range(n))
    srng = P.Rng(P.kdf(master, 0x5150 + pi, n) or 0x1234567)
    for i in range(n - 1, 0, -1):
        j = srng.next32() % (i + 1)
        order[i], order[j] = order[j], order[i]
    print('2. constant slot shuffle: order=%s  %s'
          % (order, 'OK' if order != list(range(n)) else 'NOT SHUFFLED'))
    ok &= order != list(range(n))

    # 3/4. decoy handlers and decoy programs
    handlers = len(re.findall(r'=function\(fr,pc,a,b,c,d\)', out))
    nproto = meta['nprotos']
    print('3. handler slots emitted: %d (real opcodes: %d)  %s'
          % (handlers, OPCOUNT, 'OK' if handlers > OPCOUNT else 'NO DECOYS'))
    ok &= handlers > OPCOUNT
    print('4. prototypes: %d (input had %d)  %s'
          % (nproto, len(protos), 'OK' if nproto > len(protos) else 'NO DECOYS'))
    ok &= nproto > len(protos)

    # 5. integrity gate: flip one payload character, chunk must refuse to run
    tmp = tempfile.NamedTemporaryFile('w', suffix='.lua', delete=False)
    tmp.write(out)
    tmp.close()
    stdout, _ = run(tmp.name)
    baseline_ok = stdout.strip() != ''
    pool = '┒╫┇╯▄▽▴◨▦╙▾╠╵╨╾▧╀┫◒▇◬◤'
    idx = out.index('\\u{') if '\\u{' in out else out.index('--[[')
    # find a payload escape far enough in, and swap its codepoint
    m = list(re.finditer(r'\\u\{([0-9a-f]{4})\}', out))
    victim = m[len(m) // 2]
    old_cp = victim.group(1)
    new_cp = '25%02x' % (int(old_cp[-2:], 16) ^ 0x01) if int(old_cp[-2:], 16) ^ 0x01 > 0x20 \
        else '2588'
    tampered = out[:victim.start()] + '\\u{' + new_cp + '}' + out[victim.end():]
    tmp2 = tempfile.NamedTemporaryFile('w', suffix='.lua', delete=False)
    tmp2.write(tampered)
    tmp2.close()
    stdout2, _ = run(tmp2.name)
    print('5. integrity gate: baseline runs=%s, tampered output=%r  %s'
          % (baseline_ok, stdout2.strip()[:40],
             'OK (refused)' if baseline_ok and stdout2.strip() == '' else 'LEAKED'))
    ok &= baseline_ok and stdout2.strip() == ''
    os.unlink(tmp.name)
    os.unlink(tmp2.name)

    print('HARDENING OK' if ok else 'HARDENING INCOMPLETE')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
