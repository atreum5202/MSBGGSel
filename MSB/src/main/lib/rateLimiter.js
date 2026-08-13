class RateLimiter {
  constructor(options = {}) {
    this.windowMs = options.windowMs || 60000; // 1 minute default
    this.maxRequests = options.maxRequests || 100;
    this.clients = new Map();
    this.cleanupInterval = setInterval(() => this.cleanup(), this.windowMs);
  }

  getKey(req) {
    const ip = req.headers['x-forwarded-for'] || req.ip || 'unknown';
    const token = req.headers['authorization'] || 'none';
    return `${ip}:${token}`;
  }

  isAllowed(req) {
    const key = this.getKey(req);
    const now = Date.now();
    
    if (!this.clients.has(key)) {
      this.clients.set(key, { count: 1, resetAt: now + this.windowMs });
      return { allowed: true, remaining: this.maxRequests - 1 };
    }

    const client = this.clients.get(key);
    
    if (now > client.resetAt) {
      client.count = 1;
      client.resetAt = now + this.windowMs;
      return { allowed: true, remaining: this.maxRequests - 1 };
    }

    if (client.count >= this.maxRequests) {
      return { 
        allowed: false, 
        remaining: 0,
        resetAt: client.resetAt
      };
    }

    client.count++;
    return { allowed: true, remaining: this.maxRequests - client.count };
  }

  cleanup() {
    const now = Date.now();
    for (const [key, client] of this.clients.entries()) {
      if (now > client.resetAt) {
        this.clients.delete(key);
      }
    }
  }

  middleware() {
    return async (req, reply) => {
      const result = this.isAllowed(req);
      
      if (!result.allowed) {
        reply.code(429).send({
          ok: false,
          error: 'Too many requests',
          resetAt: result.resetAt
        });
        return;
      }
      
      reply.header('X-RateLimit-Limit', this.maxRequests);
      reply.header('X-RateLimit-Remaining', result.remaining);
      reply.header('X-RateLimit-Reset', result.resetAt);
    };
  }

  destroy() {
    clearInterval(this.cleanupInterval);
  }
}

export function createRateLimiter(options) {
  return new RateLimiter(options);
}

export default RateLimiter;
