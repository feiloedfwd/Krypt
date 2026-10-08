#!/usr/bin/env python3
"""KRYPTVM localhost web UI - stdlib only, runs on Termux.

    python3 server.py            # 0.0.0.0:8765
    python3 server.py --port 9000

Open http://localhost:8765/ in the phone's browser, upload a .lua/.luau file,
pick options, hit Obfuscate, download the result.
"""
import argparse
import errno
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from obfuscate import __version__, obfuscate_source, ObfuscationError  # noqa: E402

STATIC = os.path.join(HERE, 'static')
OUTDIR = os.path.join(HERE, 'output')
MAX_BODY = 32 * 1024 * 1024          # 32 MB upload ceiling
LOCK = threading.Lock()
JOBS = {'count': 0, 'bytes_in': 0, 'bytes_out': 0}

MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
        '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml',
        '.ico': 'image/x-icon', '.json': 'application/json'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'kryptvm/' + __version__
    protocol_version = 'HTTP/1.1'

    # ---------------- helpers ----------------
    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), 'application/json; charset=utf-8')

    def log_message(self, fmt, *args):          # quiet-ish, still useful on Termux
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

    # ---------------- routes ----------------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html'):
            return self._serve_file(os.path.join(STATIC, 'index.html'))
        if path == '/api/health':
            return self._json(200, {'ok': True, 'version': __version__,
                                    'jobs': JOBS['count']})
        if path == '/favicon.ico':
            return self._send(204, b'', 'image/x-icon')
        safe = os.path.normpath(path.lstrip('/'))
        if safe.startswith('..') or os.path.isabs(safe):
            return self._json(403, {'ok': False, 'error': 'forbidden'})
        cand = os.path.join(STATIC, safe)
        if os.path.isfile(cand):
            return self._serve_file(cand)
        return self._json(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        path = self.path.split('?', 1)[0]
        if path != '/api/obfuscate':
            return self._json(404, {'ok': False, 'error': 'not found'})
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return self._json(400, {'ok': False, 'error': 'bad content-length'})
        if length <= 0:
            return self._json(400, {'ok': False, 'error': 'empty request'})
        if length > MAX_BODY:
            return self._json(413, {'ok': False,
                                    'error': 'file too large (max %d MB)'
                                             % (MAX_BODY // (1024 * 1024))})
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw.decode('utf-8'))
        except Exception:
            return self._json(400, {'ok': False, 'error': 'invalid JSON body'})

        code = req.get('code')
        if not isinstance(code, str) or not code.strip():
            return self._json(400, {'ok': False, 'error': 'no source code received'})
        filename = str(req.get('filename') or 'input.lua')
        filename = os.path.basename(filename).replace('\x00', '') or 'input.lua'
        level = str(req.get('level') or 'heavy')
        raw_unicode = bool(req.get('raw_unicode'))
        keep = req.get('keep', True) is not False
        seed = req.get('seed')
        try:
            seed = int(seed) if seed not in (None, '', 'random') else None
        except (TypeError, ValueError):
            return self._json(400, {'ok': False, 'error': 'seed must be an integer'})
        if seed is not None and not (0 <= seed <= 0x7FFFFFFF):
            return self._json(400, {'ok': False,
                                    'error': 'seed must be between 0 and 2147483647'})
        if level not in ('light', 'heavy', 'insane'):
            return self._json(400, {'ok': False, 'error': 'bad level'})

        try:
            with LOCK:
                res = obfuscate_source(code, seed=seed, level=level,
                                       raw_unicode=raw_unicode)
        except ObfuscationError as exc:
            return self._json(200, {'ok': False, 'error': str(exc)})
        except Exception:
            sys.stderr.write(traceback.format_exc())
            return self._json(500, {'ok': False,
                                    'error': 'internal error - see server log'})

        base = filename
        for ext in ('.luau', '.lua', '.txt'):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
                break
        outname = (base or 'input') + '.krypt.lua'

        if keep:                                 # keep a copy on disk for Termux users
            try:
                os.makedirs(OUTDIR, exist_ok=True)
                with open(os.path.join(OUTDIR, outname), 'w', encoding='utf-8') as fh:
                    fh.write(res['source'])
            except OSError:
                pass

        with LOCK:
            JOBS['count'] += 1
            JOBS['bytes_in'] += res['in_bytes']
            JOBS['bytes_out'] += res['out_bytes']

        stats = {k: v for k, v in res.items() if k != 'source'}
        stats['outname'] = outname
        stats['saved'] = keep
        return self._json(200, {'ok': True, 'stats': stats, 'output': res['source']})

    # ---------------- static files ----------------
    def _serve_file(self, path):
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError:
            return self._json(404, {'ok': False, 'error': 'not found'})
        ext = os.path.splitext(path)[1].lower()
        return self._send(200, data, MIME.get(ext, 'application/octet-stream'))


def listen(host, port, tries=25):
    """Bind the first free port starting at `port` (Termux sessions often hold
    an older server, which shows up as Errno 98 / EADDRINUSE)."""
    busy = []
    for p in range(port, port + tries):
        try:
            srv = ThreadingHTTPServer((host, p), Handler)
            srv.daemon_threads = True
            return srv, p, busy
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                busy.append(p)
                continue
            raise
    raise SystemExit(
        'no free port in %d..%d - everything there is busy.\n'
        'Pass another one, e.g.  python3 server.py --port 9000'
        % (port, port + tries - 1))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='server.py',
                                 description='KRYPTVM localhost obfuscator UI')
    ap.add_argument('-p', '--port', type=int, default=8765)
    ap.add_argument('--host', default='0.0.0.0')
    args = ap.parse_args(argv)

    srv, port, busy = listen(args.host, args.port)
    if busy:
        print('port %s already in use - an older KRYPTVM is probably still running.'
              % ', '.join(str(b) for b in busy), flush=True)
        print('  stop it with Ctrl-C in its Termux session, or run: pkill -f server.py',
              flush=True)
        print('  using port %d instead\n' % port, flush=True)
    print('KRYPTVM %s ready' % __version__, flush=True)
    print('  local : http://localhost:%d/' % port, flush=True)
    print('  lan   : http://%s:%d/' % ('<phone-ip>', port), flush=True)
    print('  output copies land in ./output/', flush=True)
    print('  Ctrl-C to stop', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nbye')
    finally:
        srv.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
