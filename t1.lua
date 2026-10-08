local a = 1
local b = 2
print("hello", a + b, a * b, a - b, a / b, a % b, 2^3)
print("str" .. "cat", #"hello")
local t = {10, 20, 30, name = "tab"}
print(t[1], t[2], t[3], #t, t.name)
t[4] = 40
print(#t, t[4])
local n = 0
for i = 1, 5 do n = n + i end
print("sum", n)
for i = 10, 1, -2 do n = n - 1 end
print("n", n)
local k = 0
while n > 0 do n = n - 2 k = k + 1 end
print("while", n, k)
local c = 0
repeat c = c + 1 until c >= 3
print("repeat", c)
if a < b then print("less") else print("more") end
local f = function(x) return x * 2 end
print("f", f(21))
local function g(x, y) if y == nil then y = 1 end return x + y end
print("g", g(1), g(1, 2))
local obj = {}
function obj:add(v) self.sum = (self.sum or 0) + v return self.sum end
print("obj", obj:add(5), obj:add(7))
local s2 = ""
for i = 1, 3 do s2 = s2 .. tostring(i) end
print("s2", s2)
print("type", type(a), type("x"), type(nil), type({}), type(print))
local mt = 0
for _, v in ipairs({5, 6, 7}) do mt = mt + v end
print("ipairs", mt)
local hk = 0
for kk, vv in pairs({x = 1, y = 2}) do hk = hk + vv end
print("pairs", hk)
print(string.format("%d-%s", 7, "z"))
print(math.floor(3.7), math.max(1, 9, 4))
local up = 100
local function counter() up = up + 1 return up end
print("counter", counter(), counter(), up)
local function outer(x)
  local y = x * 2
  return function() return y + 1 end
end
print("closure", outer(10)())
print(select("#", 1, 2, 3), select(2, "a", "b", "c"))
local function multi() return 1, 2, 3 end
local m1, m2, m3 = multi()
print("multi", m1, m2, m3)
print("multi2", multi())
local pk = {multi()}
print("pack", #pk)
print("bool", true and "yes" or "no", false and "yes" or "no", nil or "dflt")
print("num", 10 // 3, bit32.bor(5, 2))
local v = 1
v = v + 1
print("v", v)
do
  local shadow = 42
  print("scope", shadow)
end
print("done")
