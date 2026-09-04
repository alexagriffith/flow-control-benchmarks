local bucket = KEYS[1]
local reservation = KEYS[2]
local cost = tonumber(ARGV[1])
local rate_per_ms = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local existing = redis.call('HGET', reservation, 'decision')
if existing then
  local existing_balance = tonumber(redis.call('HGET', bucket, 'balance_milli') or burst)
  return {existing, existing_balance, 'existing'}
end

local now_parts = redis.call('TIME')
local now_ms = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
local balance = tonumber(redis.call('HGET', bucket, 'balance_milli') or burst)
local last_ms = tonumber(redis.call('HGET', bucket, 'last_ms') or now_ms)
local elapsed = math.max(0, now_ms - last_ms)
balance = math.min(burst, balance + elapsed * rate_per_ms)

local decision = 'overflow'
if balance >= cost then
  decision = 'reserved'
  balance = balance - cost
end

redis.call('HSET', bucket, 'balance_milli', balance, 'last_ms', now_ms)
redis.call('HSET', reservation,
  'decision', decision, 'state', decision, 'cost_milli', cost,
  'created_ms', now_ms)
redis.call('EXPIRE', reservation, ttl)
return {decision, balance, 'new'}
