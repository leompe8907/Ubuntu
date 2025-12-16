-- Token Bucket Rate Limiting Script para Redis
-- Implementa algoritmo token bucket atómico para rate limiting
-- Evita race conditions usando operaciones atómicas de Redis

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local tokens_requested = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local window_seconds = tonumber(ARGV[5])

-- Obtener estado actual del bucket
local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1]) or capacity
local last_refill = tonumber(bucket[2]) or now

-- Calcular tokens a reponer basado en tiempo transcurrido
local elapsed = now - last_refill
local tokens_to_add = math.floor(elapsed * refill_rate / window_seconds)
tokens = math.min(capacity, tokens + tokens_to_add)

-- Verificar si hay suficientes tokens
if tokens >= tokens_requested then
    -- Hay suficientes tokens: consumir y actualizar
    tokens = tokens - tokens_requested
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, window_seconds)
    return {1, tokens}  -- allowed=1, remaining tokens
else
    -- No hay suficientes tokens: actualizar estado y calcular retry_after
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, window_seconds)
    -- Calcular cuánto tiempo esperar para tener suficientes tokens
    local tokens_needed = tokens_requested - tokens
    local retry_after = math.ceil(tokens_needed / refill_rate * window_seconds)
    return {0, tokens, retry_after}  -- denied=0, remaining tokens, retry_after seconds
end

