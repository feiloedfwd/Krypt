# KRYPTVM on Termux (Android) — step by step

Everything runs on the phone. No PC, no accounts, no uploads.

## 1. Install the two packages you need

Open Termux and run:

```bash
pkg update -y
pkg install -y python git unzip
```

`python` is the whole runtime — KRYPTVM uses only the Python standard library.

## 2. Get the project onto the phone

Copy `kryptvm.zip` to the phone (Downloads folder is easiest), then:

```bash
termux-setup-storage          # one-time, see the note below
```

`termux-setup-storage` **always prints a question** like:

```
directory '~/storage' already exists. ...
Do you want to continue? (y/n)
```

Answer **`y`** (or `n` — both are fine, nothing gets deleted either way).
Do this **as its own command**, then wait for the prompt to finish before
pasting anything else.

Now unpack the project — one command, and it does not care where you are:

```bash
cd ~
unzip -o ~/storage/downloads/kryptvm.zip -d ~
```

That creates `~/kryptvm`. If the zip is somewhere else, point at it directly:

```bash
unzip -o /sdcard/Download/kryptvm.zip -d ~        # phone Downloads folder
```

Check it landed:

```bash
ls ~/kryptvm          # -> README.md  TERMUX.md  obf  obfuscate.py  server.py  static  tests  demo

## 3. Start the localhost website

```bash
cd ~/kryptvm
python3 server.py
```

(If you are ever unsure where you are, this works from anywhere too:
`python3 ~/kryptvm/server.py`.)

You will see:

```
KRYPTVM 1.1 ready
  local : http://localhost:8765/
  output copies land in ./output/
  Ctrl-C to stop
```

Open Chrome → `http://localhost:8765/`.

## 4. Use it

1. **Tap the dashed box** and pick your `.lua` / `.luau` script
   (or paste the code straight into the text box).
2. Pick `light` / `heavy` / `insane` junk density, optionally set a **seed**
   (same seed + same script = identical output).
3. Tap **OBFUSCATE**.
4. Tap **Download .lua** — the file is also saved in `~/kryptvm/output/`.

The result is one dense line of code. Paste it into a Roblox `Script` or
`LocalScript` and it just runs. You can check the file with:

```bash
ls -lh ~/kryptvm/output/
```

## 5. Command line (no browser needed)

```bash
python3 obfuscate.py myscript.lua                     # -> myscript.obf.lua
python3 obfuscate.py myscript.lua --level insane --seed 1337
python3 obfuscate.py - < myscript.lua > out.lua       # pipes work too
python3 obfuscate.py myscript.lua --pretty            # readable loader (debug)
```

## 6. Phone-specific tips

- **Keep Termux awake**: Termux → notification → *Acquire wakelock* while
  obfuscating big scripts (a 150 KB script takes about half a second, so this
  is only for huge inputs).
- **Battery**: nothing runs in the background unless you leave `server.py` on.
  Stop it with `Ctrl-C` (Termux extra keys row has it).
- **Port already in use?** From v2.1 the server just moves to the next free port
  and prints the URL. You can still force one: `python3 server.py --port 9000`.
  If you want to free the old one instead: `pkill -f server.py`
  (`pkg install -y procps` once) or Ctrl-C in the session running it.
- **Open it from another device on the same Wi-Fi**: the server prints
  `http://<phone-ip>:8765/`; make sure the other device is on the same network.
- **Copying to Roblox Studio**: use the **Download** button (a file) rather
  than the **Copy** button — the payload is full of unicode escape sequences
  and some chat apps mangle raw unicode. If you must copy/paste, stay on the
  default (escaped) mode.
- **Raw unicode mode** makes a ~40% smaller file, but the file must survive
  transfer as real UTF-8; use it when downloading straight into Studio.

## 7. Verify an output before you ship it

You can always test locally that the obfuscated chunk still does what the
original did, without Roblox, if you have a Lua/Luau interpreter:

```bash
pkg install -y lua54        # or: pkg install -y luajit
lua5.4 myscript.lua > a.txt
lua5.4 myscript.krypt.lua > b.txt
diff a.txt b.txt && echo "identical behaviour"
```

(Anything using Roblox-only APIs will obviously need Studio to run.)

## 8. Roblox gotchas (why output can differ from desktop Lua)

The obfuscator is tested against Roblox's actual constraints, but these are
worth knowing:

| Roblox behaviour | how KRYPTVM handles it |
|---|---|
| `getfenv` is **not** available to scripts | never required — the loader falls back to literal captures of the standard globals |
| `_G` is a **separate shared table**, not the script environment | both are consulted; `_G` writes still work as usual |
| the script environment serves globals through `__index` | globals are looked up with normal indexing, not `rawget` |
| one-line output means `file:1` in every error message | expected — minified code has no useful line numbers (that is the point) |
| `Shared`/custom globals from other scripts | resolve through the environment then `_G`, exactly like plain Lua |

## 9. Troubleshooting

| symptom | fix |
|---|---|
| `python3: can't open file '/data/data/com.termux/files/home/server.py'` | you are in `~` and the project is not unpacked yet — run `cd ~`, then `unzip -o ~/storage/downloads/kryptvm.zip -d ~`, then `cd ~/kryptvm`. (Or launch it directly: `python3 ~/kryptvm/server.py`) |
| pasted commands did nothing after `termux-setup-storage` | that command asks `(y/n)`. Answer it **first**, then paste the rest. Anything chained with `&&` gets eaten while the prompt waits |
| `unzip: cannot find or open .../kryptvm.zip` | the zip is not on the phone yet — download `kryptvm.zip` in the browser first, then `ls ~/storage/downloads/` to see where it landed |
| `python3: command not found` | `pkg install -y python` |
| `OSError: [Errno 98] Address already in use` | an older `python3 server.py` is still running in another Termux session. v2.1 auto-picks the next free port and tells you which one — just open the URL it prints. To stop the old one: `pkill -f server.py` (needs `pkg install -y procps`), or switch to its session and press Ctrl-C. Or force a port: `python3 server.py --port 9000` |
| page loads but OBFUSCATE spins forever | check the Termux window for a traceback; it is written to stderr |
| `line NN: expected ...` error in the UI | that is your Lua syntax, not the obfuscator — fix the script |
| Roblox: `attempt to index nil with 'X'` | you are on an old build — download the current `kryptvm.zip` (v1.2 fixed the Roblox environment proxy) |
| output is much bigger than the input | normal: `heavy` masks operands so the bytecode no longer compresses. Use `--level light` for the smallest output |
| Roblox: error shows `something:1` | normal: the payload is one line |
| file too big | 32 MB input ceiling; split the module |
| output runs but does nothing in Roblox | it will follow the original script's semantics; test the original first |

## Fast recovery (if you got lost mid-install)

Paste this block exactly as it is, all at once:

```bash
cd ~
pkg install -y python unzip
ls -lh ~/storage/downloads/kryptvm.zip     # does the zip exist? if not, download it first
unzip -o ~/storage/downloads/kryptvm.zip -d ~
cd ~/kryptvm
ls                                          # README.md  TERMUX.md  obf  server.py  static  tests
python3 server.py
```

Then open `http://localhost:8765/` in Chrome. Leave that Termux session running
while you use the page.
