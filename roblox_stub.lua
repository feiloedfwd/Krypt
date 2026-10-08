-- minimal roblox-ish API surface so t3 can run outside studio
local _svc = {}
game = { _svc = _svc }
function game:GetService(name)
  if not _svc[name] then _svc[name] = { _name = name, _e = {} } end
  return _svc[name]
end
function game:IsLoaded() return true end
local Signal = {}
Signal.__index = Signal
function Signal.new() return setmetatable({ _h = {} }, Signal) end
function Signal:Connect(f) self._h[#self._h + 1] = f return { Disconnect = function() end } end
function Signal:Fire(...) for i = 1, #self._h do self._h[i](...) end end
Instance = {}
Instance.__index = Instance
function Instance.new(cls, parent)
  local o = setmetatable({ ClassName = cls, _c = {}, _p = parent, Children = {} }, Instance)
  if parent then parent.Children[#parent.Children + 1] = o end
  return o
end
function Instance:GetChildren() return self.Children end
function Instance:Destroy() self._dead = true end
task = { wait = function() end, spawn = function(f, ...) return f(...) end,
         delay = function(_, f, ...) return f(...) end }
Vector3 = { new = function(x, y, z) return { X = x, Y = y, Z = z } end }
tick = function() return 12345 end
warn = print
