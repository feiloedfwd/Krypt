-- deeper feature test
local function counterfactory(start)
  local n = start
  return function()
    n = n + 1
    return n, n * 10
  end
end
local c1 = counterfactory(5)
print("cf", c1(), c1())
local c2 = counterfactory(100)
print("cf2", c2())

-- 3 level closures
local function lvl1(a)
  local function lvl2(b)
    local function lvl3(c)
      return a + b + c
    end
    return lvl3
  end
  return lvl2(20)
end
print("lvl", lvl1(1)(300))

-- mutual recursion
local isodd, iseven
function isodd(n) if n == 0 then return false end return iseven(n - 1) end
function iseven(n) if n == 0 then return true end return isodd(n - 1) end
print("even", iseven(10), isodd(10), iseven(7))

-- varargs
local function sum(...)
  local t = {...}
  local s = 0
  for i = 1, #t do s = s + t[i] end
  return s, select("#", ...)
end
print("sum", sum(1, 2, 3, 4))
local function fwd(...) return sum(...) end
print("fwd", fwd(10, 20))

-- string methods and interpolation
local s = "Hello World"
print("str", s:upper(), s:lower(), s:sub(1, 5), s:rep(2):len(), s:byte(1))
local who = "bob"
print(`greet {who} = {1 + 2}`)

-- conditionals
local x = 7
print("condexpr", if x > 5 then "big" else "small")
-- continue & nested loops
local acc = 0
for i = 1, 10 do
  if i % 2 == 0 then continue end
  acc = acc + i
end
print("odd sum", acc)
local grid = 0
for i = 1, 3 do
  for j = 1, 3 do
    if j == 2 then break end
    grid = grid + i * j
  end
end
print("grid", grid)

-- tables
local t = {a = 1, [2] = "two", 3, nested = {x = {y = 9}}}
print("tab", t.a, t[1], t[2], t[3], t.nested.x.y)
t.a = t.a + 41
print("tab2", t.a)
local arr = {}
for i = 1, 5 do arr[i] = i * i end
print("arr", #arr, arr[3], arr[5])

-- methods on tables
local Vec = {}
Vec.__index = Vec
function Vec.new(x, y) return setmetatable({x = x, y = y}, Vec) end
function Vec:len2() return self.x * self.x + self.y * self.y end
print("vec", Vec.new(3, 4):len2())

-- pcall / errors
local ok, err = pcall(function() error("boom") end)
print("pcall", ok, err)

-- numeric
print("num", 7 / 2, 7 // 2, 2^0.5 > 1.41, -5 % 3, 10 == 10.0)
print("cmp", 1 < 2, "a" < "b", nil == false, (1 == 1) and "y" or "n")

-- while with continue
local w = 0
local i2 = 0
while i2 < 10 do
  i2 = i2 + 1
  if i2 % 3 == 0 then continue end
  w = w + i2
end
print("whilec", w)

-- upvalue sharing between two closures
local function pair()
  local v = 0
  return function() v = v + 1 return v end, function() return v end
end
local inc, get = pair()
inc(); inc()
print("shared", get())

-- long chains & self-referential table
local conf = {level = {deep = {value = "found"}}}
print("deep", conf.level.deep.value)

-- string concat with numbers
print("cat", "n=" .. 5 .. "!", #("x" .. "y" .. "z"))
print("final")
