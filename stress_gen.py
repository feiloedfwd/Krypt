#!/usr/bin/env python3
"""Generate a large synthetic Lua program for scale testing."""
import random
import sys

n = int(sys.argv[1]) if len(sys.argv) > 1 else 400
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
r = random.Random(seed)
L = []
L.append('-- synthetic stress program: %d functions' % n)
L.append('local acc = 0')
L.append('local log = {}')
for i in range(n):
    a, b = r.randint(1, 50), r.randint(1, 40)
    L.append('do')
    L.append('local function fn%d(x, y)' % i)
    L.append('  local t = {}')
    L.append('  for k = 1, %d do t[k] = (x + k) * %d %% %d end' % (a, b, r.randint(2, 30)))
    L.append('  local s = 0')
    L.append('  for k = 1, #t do')
    L.append('    if t[k] %% 3 == 0 then s = s + t[k] else s = s - (%d) end' % r.randint(1, 9))
    L.append('  end')
    L.append('  local sub = function(z) return z * 2 + %d end' % r.randint(0, 20))
    L.append('  return s + sub(y or 1), #t')
    L.append('end')
    L.append('local v%d, c%d = fn%d(%d, %d)' % (i, i, i, r.randint(1, 90), r.randint(1, 90)))
    L.append('acc = acc + v%d + c%d' % (i, i))
    L.append('log[#log + 1] = ("fn%d="):format(v%d)' % (i, i))
    L.append('end')
L.append('print("stress", %d, acc, #log, log[1], log[#log])' % n)
L.append('local words = {"alpha","beta","gamma","delta"}')
L.append('local joined = table.concat(words, "-")')
L.append('print("tab", joined, #joined, words[2]:upper())')
L.append('local mt = {__add = function(a, b) return a.v + b.v end}')
L.append('local p1 = setmetatable({v = 21}, mt)')
L.append('local p2 = setmetatable({v = 21}, mt)')
L.append('print("meta", p1 + p2)')
L.append('local ok, err = pcall(function() error("kaboom") end)')
L.append('print("pcall", ok, type(err))')
L.append('print("status", "loaded", 42, nil, true)')
print('\n'.join(L))
