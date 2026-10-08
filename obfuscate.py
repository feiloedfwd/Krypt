#!/usr/bin/env python3
"""KRYPTVM - Lua/Luau (Roblox) source -> VM-bytecode obfuscator.

Library use:
    from obfuscate import obfuscate_source
    result = obfuscate_source(code, seed=1234, level='heavy', raw_unicode=False)

CLI use:
    python3 obfuscate.py input.lua -o output.lua --seed 1234 --level heavy
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from obf.compiler import compile_ast, CompileError, OPCOUNT   # noqa: E402
from obf.frontend import parse, ParseError, LexError   # noqa: E402
from obf.runtime import RuntimeGen, generate, generate_with_meta  # noqa: E402

__version__ = '3.0'

LEVELS = ('light', 'heavy', 'insane')


class ObfuscationError(Exception):
    pass


def obfuscate_source(code, seed=None, level='heavy', raw_unicode=False,
                     minify_output=True):
    """Compile `code` to bytecode and emit a self-contained obfuscated chunk.

    Returns a dict: {source, seed, level, raw_unicode, protos, instructions,
                     consts, closures, in_bytes, out_bytes, seconds}
    """
    if level not in LEVELS:
        raise ObfuscationError('level must be one of: %s' % ', '.join(LEVELS))
    if seed is None:
        seed = int.from_bytes(os.urandom(4), 'big')
    seed = int(seed) & 0x7FFFFFFF

    t0 = time.time()
    try:
        ast = parse(code)
    except (ParseError, LexError) as exc:
        raise ObfuscationError(str(exc))
    try:
        protos = compile_ast(ast)
    except CompileError as exc:
        raise ObfuscationError(str(exc))
    if len(protos) == 0 or len(protos[0].code) == 0:
        raise ObfuscationError('nothing to obfuscate (empty module)')
    src, meta = generate_with_meta(protos, seed=seed, raw_unicode=raw_unicode,
                                   obf_level=level, minify_output=minify_output)

    return {
        'source': src,
        'seed': seed,
        'level': level,
        'raw_unicode': raw_unicode,
        'protos': meta.get('nprotos', len(protos)),
        'real_protos': len(protos),
        'instructions': sum(len(p.code) for p in protos),
        'consts': sum(len(p.consts) for p in protos),
        'closures': sum(1 for p in protos if p.parent is not None),
        'opcodes': OPCOUNT,
        'minified': bool(minify_output),
        'layers': ('unicode-junk > RC4 > block-shuffle > bit-plane-split > '
                   'xor/add/rot/nibble > 2x keyed chained cells (each cell keyed to the '
                   'previous plaintext) > LZ77 > integrity gate > per-proto opcode '
                   'scrambling > masked operands > lazy per-constant encrypted constants '
                   'with shuffled slots > shuffled protos + decoy programs + decoy opcode '
                   'handlers > shuffled-opcode VM'),
        'in_bytes': len(code.encode('utf-8')),
        'out_bytes': len(src.encode('utf-8')),
        'payload_bytes': meta['enc_len'],
        'raw_bytes': meta['raw_len'],
        'seconds': round(time.time() - t0, 3),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='obfuscate.py',
        description='KRYPTVM - heavy Lua/Luau obfuscator (custom compiler + bytecode VM)')
    ap.add_argument('input', help='input .lua / .luau file ("-" for stdin)')
    ap.add_argument('-o', '--output', help='output file (default: <input>.obf.lua)')
    ap.add_argument('-s', '--seed', type=int, default=None,
                    help='deterministic seed (default: random)')
    ap.add_argument('-l', '--level', choices=LEVELS, default='heavy',
                    help='junk/dead-code density (default: heavy)')
    ap.add_argument('--raw', action='store_true',
                    help='emit raw unicode junk instead of \\u{..} escapes')
    ap.add_argument('--pretty', action='store_true',
                    help='indented, human-readable loader (debugging only)')
    ap.add_argument('-q', '--quiet', action='store_true')
    args = ap.parse_args(argv)

    if args.input == '-':
        code = sys.stdin.read()
        name = 'stdin'
    else:
        with open(args.input, 'r', encoding='utf-8', errors='surrogateescape') as fh:
            code = fh.read()
        name = args.input

    try:
        res = obfuscate_source(code, seed=args.seed, level=args.level,
                               raw_unicode=args.raw,
                               minify_output=not args.pretty)
    except ObfuscationError as exc:
        sys.stderr.write('error: %s\n' % exc)
        return 1

    if args.output:
        out = args.output
    elif args.input == '-':
        out = 'obfuscated.lua'
    else:
        base = args.input
        for ext in ('.luau', '.lua'):
            if base.endswith(ext):
                base = base[:-len(ext)]
                break
        out = base + '.obf.lua'
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(res['source'])

    if not args.quiet:
        sys.stderr.write(
            'kryptvm %s  %s -> %s\n'
            '  seed %d | level %s | %s\n'
            '  %d protos, %d instructions, %d constants\n'
            '  %d B -> %d B (payload %d B, bc %d B) in %.3fs\n'
            % (__version__, name, out, res['seed'], res['level'],
               'raw unicode' if res['raw_unicode'] else 'escaped',
               res['protos'], res['instructions'], res['consts'],
               res['in_bytes'], res['out_bytes'], res['payload_bytes'],
               res['raw_bytes'], res['seconds']) + '  minified\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
