local bucket = KEYS[1]
local reservation = KEYS[2]
local action = ARGV[1]
local burst = tonumber(ARGV[2])

local decision = redis.call('HGET', reservation, 'decision')
local state = redis.call('HGET', reservation, 'state')
if not decision then
  return {'missing', 0}
end
if decision ~= 'reserved' then
  return {state, tonumber(redis.call('HGET', bucket, 'balance_milli') or burst)}
end
if state ~= 'reserved' then
  return {state, tonumber(redis.call('HGET', bucket, 'balance_milli') or burst)}
end

local balance = tonumber(redis.call('HGET', bucket, 'balance_milli') or burst)
if action == 'release' then
  local cost = tonumber(redis.call('HGET', reservation, 'cost_milli') or 0)
  balance = math.min(burst, balance + cost)
  redis.call('HSET', bucket, 'balance_milli', balance)
  state = 'released'
elseif action == 'settle' then
  state = 'settled'
else
  return {'invalid_action', balance}
end
redis.call('HSET', reservation, 'state', state)
return {state, balance}
