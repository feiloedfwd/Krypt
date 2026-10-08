# KRYPTVM

A heavy Lua / Luau (Roblox) obfuscator with its **own compiler and bytecode VM**.
Runs entirely on-device — designed to work on a phone in **Termux**, with a
localhost web page for uploads/downloads.

```
your.lua ──▶ lexer/parser ──▶ custom compiler ──▶ bytecode
                                                   │
   shuffle protos + decoy programs, mask operands, encrypt each constant
                                                   │
   LZ77 ─▶ chained cells (each key depends on the previous plaintext)
                                                   │
   xor/add/rot/nibble ─▶ bit planes ─▶ RC4 ─▶ block shuffle
                                                   │
      unicode junk encoding ──▶ ONE DENSE LINE of code (minified, no formatting)
                                                   │
   at runtime: 11 reversed layers ──▶ shuffled-opcode VM ──▶ your program runs
```

> New here? **`TERMUX.md`** is the step-by-step phone tutorial.

## Quick start (Termux)

```bash
pkg install python            # once
cd ~/kryptvm
python3 server.py             # starts the localhost UI
```

Open the printed address in the phone browser (`http://localhost:8765/`),
tap **Choose file**, pick your `.lua`/`.luau` script, hit **OBFUSCATE**, then
**Download .lua**. A copy also lands in `./output/`.

Options in the UI:

| option | what it does |
|---|---|
| **level** `light / heavy / insane` | junk + dead code density **and** operand-mask strength (`light` = smallest, `insane` = strongest) |
| **seed** | deterministic output (blank = random). Same seed + same input ⇒ same file |
| **chunk size** | payload string chunking (bigger = fewer concatenations) |
| **raw unicode** | emit real `▚╫┇` characters instead of `\u{..}` escapes (smaller file) |
| **output** | always minified to a single line; `--pretty` on the CLI for a readable loader |

### Command line

```bash
python3 obfuscate.py script.lua -o out.lua            # defaults: heavy, random seed
python3 obfuscate.py script.lua --seed 1234 --level insane --raw
python3 obfuscate.py - < script.lua > out.lua         # stdin / stdout
```

## What the output looks like

Everything the original script contained — names, strings, control flow,
constants — is gone. What remains is a decoder, a mangled VM, and a wall of
unicode junk:

```lua
--┒╫┇╯┄▽▴◨▦╙▾╠╵╨╾▧╀┫◒▇◬◤╮□◤╩╧▸╎┄◳┴╡◲◷┺△◢◡┓┸┞▒▰▼▓┽╤◖─▁┹╵◈╫◫◮┙╕◲╊▝►╓╷┨◗┳▍╲▤▯╿┿
local _ErfgUyB, _EoFogNCA = string.byte, string.sub
local _TYdXtyNB = "\u{258e}\u{25ba}\u{25d0}\u{250d}…"
...
local function _YPFcv(s, base) ... end            -- de-junk
local function _JzkijuBR(s, key) ... end         -- xor layer
local function _build() ... end                  -- proto decoder
_RGDfcjDm[17] = function(fr, pc, a, b, c, d) ... end   -- one VM opcode
```

Protection layers, in order:

1. **Custom compiler** — Lua/Luau → 35-opcode register bytecode. No Lua source,
   no Lua bytecode (`string.dump` format), no `loadstring` of anything readable.
2. **Encryption** — LZ77 → **chained cells** (the payload is cut into 640-byte
   cells and each cell is RC4'd with a key derived from the *previous cell's
   plaintext*, so no cell can be decoded without all the ones before it) → four
   pointwise layers (nibble swap, rotate, add, xor) → **bit-plane split**
   → **RC4** → **64-byte block shuffle**.
3. **Unicode junk encoding** — every byte becomes a codepoint in the
   `U+2501…U+2600` block, emitted either escaped (`\u{258e}`, for safe pasting)
   or raw. The payload reads as pure decorative garbage, and it is split into
   an even/odd pair of chunk arrays that the loader interleaves back together.
4. **Shuffled opcodes** — a per-file permutation of the 6-bit opcode space, so
   opcode numbers mean nothing across two obfuscations of the same script.
5. **Mangled loader** — every internal identifier (decoder, VM, opcode table,
   environment proxy) is renamed per file.
6. **Junk / dead code** — dense single-line unicode banner blocks at the head
   and tail, decoy string tables, plausible-looking dead helper functions and
   permanently-false branches, at `light` / `heavy` / `insane` density.
7. **Minified output** — the emitted chunk is tokenised and joined with the
   minimum separators (comments stripped, no indentation), so there is no
   formatting to read and nothing to collapse by hand.

### v3.0 — second round of hardening

A second deobfuscation attempt (an LLM, 4 minutes) recovered the whole layer
stack and the VM skeleton but **could not reconstruct the program**: it stalled
at the data layer. Its report listed what was still readable — the opcode table,
"most VM operations", and the prototype count. v3.0 targets exactly that:

| still readable in v2 | v3.0 |
|---|---|
| one global opcode table | **per-proto opcode codebooks** — every prototype scrambles the 6-bit opcode space with its own key-derived permutation; there is no table to read |
| handler bodies reveal the ISA | **decoy opcode handlers**: the handler array holds 12 extra plausible-looking bodies that no opcode id selects, emitted in shuffled order |
| one chained-cell layer, fixed 640-byte cells | **two chained layers**, cell sizes derived from the chain secret — a replay that fumbles a cell cannot resynchronise |
| tamper/partial recovery is silent | **integrity gate**: the decoded bytecode is hashed and compared against a chain-derived value; any single-byte edit makes the chunk refuse to run rather than execute something wrong |
| constant slots follow instruction order | **shuffled constant slots** per prototype |

`tests/hardening_check.py` asserts every claim above on real output, including a
live tamper test:

```
1. per-proto opcode codebook: 64/64 positions differ between protos  OK
2. constant slot shuffle: order=[6, 2, 3, 1, 0, 7, 4, 5]             OK
3. handler slots emitted: 48 (real opcodes: 35)                      OK
4. prototypes: 20 (input had 8)                                      OK
5. integrity gate: baseline runs=True, tampered output=''            OK (refused)
HARDENING OK
```

### v2.0 — what changed after a deobfuscation attempt

A public LLM recovered a v1.2 output in about a minute by replaying the decode
layers and reading the constant pool ("`math`", "`random`", `1`, `100`,
"`print`"). v2.0 closes each of the holes that made that possible:

| hole v1.2 had | v2.0 |
|---|---|
| every constant decrypted up-front into a plain table | **lazy per-constant decryption**: each constant is its own encrypted cell (key derived from the chain secret), decrypted on **first use** and then the ciphertext is **wiped** |
| strings/numbers readable in the decoded bytecode | strings are **split in two** encrypted fragments, numbers are keystream-masked — a static dump shows no literals at all |
| one flat payload stream | **chained cells**: cell *n*'s key depends on cell *n-1*'s plaintext |
| fixed, guessable instruction layout | **masked operands** (none / per-proto / per-field, by level) |
| 17 readable instructions, nothing else | **shuffled protos + decoy programs + decoy constants + unreachable decoy instructions** — a dump shows several plausible programs |
| loader structure is the map of the format | identifiers renamed per file, keys never stored as literals |

`tests/leak_check.py` reproduces the strongest *static* replay (peel every
layer, break the cells — given the master secret — then scan the result) and
asserts that **no literal from the input program survives**:

```
t1.lua   decoded=3847 B  printable-runs=4  leaked-literals=0
t2.lua   decoded=4911 B  printable-runs=5  leaked-literals=0
t5.lua   decoded=3166 B  printable-runs=2  leaked-literals=0
NO LEAKS
```

**Honest limit:** no self-contained Luau obfuscator is unbeatable. Anything the
loader can compute, a determined analyst (or an AI reading the loader) can
recompute *given enough time*, and anything that runs can be dumped from memory
at runtime. What v2.0 does is remove every shortcut: constants only exist as
plaintext momentarily, the payload cannot be decoded out of order, and the
program is buried among decoys. Beating it now means reimplementing the whole
KDF chain correctly, instrumenting first-use constant decryption, and
separating real from decoy code — hours of careful work instead of a
59-second replay. Raise `--level insane` for the strongest (largest) output.

8. **Environment proxy** — globals resolve through the script environment,
   then a literal capture of the standard library, then `_G`, so
   `game:GetService(...)`, `Instance.new(...)`, `math.random(...)` etc. work
   exactly as in Roblox (no dependency on `getfenv`, which Roblox hides).

## Verified behaviour

The obfuscated output is checked against the **real Luau interpreter**, not
against the Python model: every test script is run plain and obfuscated and the
outputs are compared byte for byte.

```bash
python3 tests/obf_check.py tests/t1.lua tests/t2.lua tests/t3.lua tests/t4.lua
python3 tests/env_check.py tests/t1.lua tests/t2.lua tests/t3.lua tests/t4.lua tests/t5.lua
python3 tests/leak_check.py tests/t1.lua tests/t2.lua tests/t5.lua
python3 tests/hardening_check.py
```

`env_check.py` exists because Roblox differs from desktop Luau in ways that
break naive VM loaders: `getfenv` is unavailable, and `_G` is a separate shared
table rather than the script environment.


| test | covers |
|---|---|
| `tests/t1.lua` | arithmetic / strings / tables / loops / closures / varargs / metatables |
| `tests/t2.lua` | nested closures, mutual recursion, `continue`, string methods, interpolation, `pcall` |
| `tests/t3.lua` | Roblox-shaped code (`game:GetService`, `Instance.new`, signals, `task.*`) against a stubbed API |
| `tests/t4.lua` | register pressure (100+ live locals in one function) and 40/60-element tables |
| `tests/t5.lua` | stdlib/global access (`math`, `string`, `os`, `pairs`, user globals) the way Roblox scripts use it |
| `tests/env_check.py` | runs every script under 5 Roblox-like environments: no `getfenv`, `getfenv`→nil, sandboxed `__index`-only env, `_G`-only env |
| `tests/leak_check.py` | replays the strongest static attack and asserts no program literal survives |
| `tests/hardening_check.py` | asserts per-proto opcodes, slot shuffling, decoys and the integrity gate are live |
| `obf/minify.py` | the minifier itself is differential-tested against Luau on the test scripts |
| `tests/stress_gen.py` | generates a 5 000-line, 148 KB program (803 protos, 23 677 instructions) |

Current status: **all of the above pass** in both encodings across multiple
seeds; the only intended difference is that `error()` messages point at the
obfuscated chunk instead of the original file (unavoidable for any VM
obfuscator and harmless in practice).

## Layout

```
obf/frontend.py    Lua 5.1 / Luau lexer, parser, AST, unparser
obf/compiler.py    AST → bytecode compiler (register VM, 35 opcodes)
obf/packer.py      serializer, LZ77, encryption layers, unicode junk encoder
obf/runtime.py     the loader generator (decoder + VM + junk/dead-code layers)
obf/minify.py      token-level Lua minifier (used for the emitted chunk)
obf/vm.py          Python reference VM (development / differential testing)
obfuscate.py       library entry point + CLI
server.py          localhost HTTP server (stdlib only)
static/index.html  the mobile web UI
TERMUX.md          step-by-step Termux (Android) tutorial
tests/             differential tests + harnesses
output/            copies of everything you generate in the UI
demo/              example obfuscated chunk
```

## Notes

- Output is a single self-contained chunk; paste it into a `Script`/`LocalScript`,
  or `require` it. It uses `bit32`, `getfenv`, `table.unpack` — all present in
  Roblox Luau.
- No network access, no telemetry, no build step: the phone does all the work.
- `raw unicode` produces a smaller file but the file must survive transfer as
  UTF-8; use the default (`\u{..}` escapes) if you copy/paste through chat apps.
- Keep your seeds if you want reproducible builds.
