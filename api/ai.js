// YAKK Studios — Anthropic AI Proxy
// Route: /api/ai  (POST)
// Proxies Claude API calls server-side so the API key is NEVER exposed
// in client-side source code. Set ANTHROPIC_API_KEY in Vercel env vars.
//
// SECURITY NOTES (from YAKK Security Audit v1.1):
//   - API key stored in Vercel environment variable — NOT in source code
//   - Rate limiting: 10 requests per IP per minute
//   - Input validation: max 2000 chars per message, max 20 history items
//   - No streaming (simplifies security surface)
//   - CORS restricted to yakkstudios.com domains

/* Security Audit v1.2: removed old yakkstudios.com domain from allowlist */
const ALLOWED_ORIGINS = [
  'https://yakkstudios.xyz',
  'https://www.yakkstudios.xyz',
];

/* Simple in-memory rate limiter (resets per serverless cold start) */
const rateLimits = new Map();
function isRateLimited(ip) {
  const now = Date.now();
  const windowMs = 60_000; // 1 minute window
  const maxRequests = 10;
  if (!rateLimits.has(ip)) { rateLimits.set(ip, []); }
  const times = rateLimits.get(ip).filter(t => now - t < windowMs);
  rateLimits.set(ip, times);
  if (times.length >= maxRequests) return true;
  times.push(now);
  return false;
}

export default async function handler(req, res) {
  const origin = req.headers.origin || '';

  // CORS — restrict to yakk domains in production
  if (ALLOWED_ORIGINS.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  } else {
    // Allow all in preview/dev deployments
    res.setHeader('Access-Control-Allow-Origin', '*');
  }
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  // Rate limiting
  const ip = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || req.socket?.remoteAddress || 'unknown';
  if (isRateLimited(ip)) {
    return res.status(429).json({ error: 'Too many requests. Slow down, degen. 😈' });
  }

  // API key from environment variable (set in Vercel dashboard)
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: 'ANTHROPIC_API_KEY not configured in environment variables.' });
  }

  // Parse and validate body
  let body;
  try {
    body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
  } catch {
    return res.status(400).json({ error: 'Invalid JSON body' });
  }

  const { model = 'claude-sonnet-4-20250514', messages, system, max_tokens = 400 } = body;

  // Input validation
  if (!Array.isArray(messages) || messages.length === 0) {
    return res.status(400).json({ error: 'messages array required' });
  }
  if (messages.length > 20) {
    return res.status(400).json({ error: 'Max 20 messages per request' });
  }
  for (const m of messages) {
    if (!m.role || !m.content) return res.status(400).json({ error: 'Each message needs role + content' });
    if (typeof m.content !== 'string') return res.status(400).json({ error: 'message content must be a string' });
    if (m.content.length > 4000) return res.status(400).json({ error: 'Message too long (max 4000 chars)' });
  }
  if (max_tokens > 1500) return res.status(400).json({ error: 'max_tokens capped at 1500' });

  // Allowed models only
  const allowedModels = ['claude-sonnet-4-20250514', 'claude-haiku-4-5-20251001'];
  if (!allowedModels.includes(model)) {
    return res.status(400).json({ error: 'Model not allowed' });
  }

  const anthropicBody = { model, max_tokens, messages };
  if (system && typeof system === 'string' && system.length <= 3000) {
    anthropicBody.system = system;
  }

  try {
    const upstream = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify(anthropicBody),
    });

    if (!upstream.ok) {
      const errData = await upstream.json().catch(() => ({}));
      return res.status(upstream.status).json({ error: errData.error?.message || 'Anthropic API error' });
    }

    const data = await upstream.json();
    return res.status(200).json({
      content: data.content,
      usage: data.usage,
    });

  } catch (err) {
    return res.status(502).json({ error: 'Proxy error: ' + err.message });
  }
}
