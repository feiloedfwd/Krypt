-- global / environment test: exercises stdlib access the way Roblox scripts do
-- (everything printed is deterministic, so obfuscated output must match exactly)
print("lib", type(math), type(string), type(table), type(os), type(coroutine))
local r = math.random(1, 100)
print("range", r >= 1 and r <= 100, math.floor(r) == r)
local d = math.random()
print("dec", d >= 0 and d < 1)
print("fmt", string.format("%d-%s-%.1f", 7, "x", 2.5))
local t = { 3, 1, 2 }
table.sort(t)
print("sorted", table.concat(t, ","), #t, math.max(9, 4), math.min(9, 4))
local n = 0
for i = 1, 5 do n = n + math.random(1, 10) end
print("sum", n > 0, n == n)
print("os", type(os.time()), os.time() > 0, type(os.clock()) == "number")
print("meta", getmetatable("").__index == string)
print("stringfns", ("abc"):upper(), string.rep("y", 2), string.sub("hello", 2, 4))
print("mathfns", math.abs(-3), math.ceil(1.2), math.sqrt(9), math.pi > 3)
userglob = "defined-here"
print("userglob", userglob)
Count = 0
for i = 1, 3 do Count = Count + 1 end
print("count", Count)
print("pairs", (function() local c = 0 for k, v in pairs({ a = 1, b = 2 }) do c = c + v end return c end)())
print("ipairs", (function() local c = 0 for i, v in ipairs({ 5, 6, 7 }) do c = c + v end return c end)())
print("tostring", tostring(12), tostring(nil), tostring(true))
print("tonumber", tonumber("42") + 1, tonumber("z") == nil)
print("select", select("#", 1, 2, 3), select(2, "a", "b", "c"))
print("pcall", pcall(function() error("x") end))
print("assert", pcall(assert, false, "msg"))
print("typeof", typeof({}) == "table", typeof(1) == "number")
print("done5")
