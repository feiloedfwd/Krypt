-- roblox-flavoured test (uses the stubbed API from tests/roblox_stub.lua)
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
print("svc", Players._name, RunService._name, game:IsLoaded())

local hits = 0
local s = (function()
  local S = {}
  local self = setmetatable({ _h = {} }, { __index = S })
  return self
end)()
s._h[#s._h + 1] = function(a, b) hits = hits + a + b end
for i = 1, #s._h do s._h[i](2, 3) end
print("signal", hits)

local part = Instance.new("Part")
part.Name = "Baseplate"
part.Size = Vector3.new(4, 1, 2)
local folder = Instance.new("Folder", part)
print("inst", part.ClassName, part.Name, part.Size.X, part.Size.Y, part.Size.Z, #part:GetChildren())

local acc = {}
for i = 1, 5 do
  if i % 2 == 0 then continue end
  acc[#acc + 1] = i * i
end
print("acc", table.concat(acc, ","))

local function wait_for(pred, n)
  for _ = 1, 3 do
    if pred() then return true end
  end
  return false
end
print("wait", wait_for(function() return hits > 0 end), wait_for(function() return false end))

local ok, err = pcall(function() error("roblox boom") end)
print("pcall", ok, type(err))

local t = { a = 1, b = { c = { d = 42 } } }
t.b.c.e = "deep"
print("chain", t.a, t.b.c.d, t.b.c.e, #t.b.c.e)

local n, m = 7, 3
print("ops", n + m, n - m, n * m, n / m, n % m, n ^ 2, -n, n .. "x")

do
  local sum = 0
  local i = 0
  while true do
    i = i + 1
    if i > 6 then break end
    if i == 3 then continue end
    sum = sum + i
  end
  print("afterr", sum, i)
end
print("done3")
