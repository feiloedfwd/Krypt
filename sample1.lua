-- sample Roblox-ish script
local Players = game:GetService("Players")
local plr = Players.LocalPlayer
local t = {1, 2, 3, x = 5, ["y z"] = 6}
local s = "hello" .. 'w' .. [[long
string]]
print(s, #t, t.x)
local function add(a, b: number): number
	return a + b
end
local n = 0
for i = 1, 10, 2 do n += i end
for k, v in pairs(t) do
	if type(v) == "number" then
		n = n * 2
	elseif v == nil then
		continue
	end
end
while n > 0 do n -= 1; if n == 5 then break end end
repeat n = n + 1 until n >= 3
local f = function(...) return select("#", ...) end
local obj = {}
function obj:method(a) return self, a end
function obj.static(a) return a end
print(obj:method(1))
print(add(1,2), f(1,2,3), `interp {n} and {"x"}`)
while true do break end

type Foo = { a: number, b: string? }
export type Bar = Foo | number

local x = if n > 2 then "big" else "small"
print(x, 0x10, 1e3, 3 // 2, 2^10, 5 % 3, 1 << 4, 7 & 3, 5 | 2, ~0)
print(not x == y, -x, #s)
do
	local a = 1
	print(a)
end
goto skip
::skip::
print("done", "end")
