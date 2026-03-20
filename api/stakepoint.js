// YAKK Studios — StakePoint API Proxy
// Route: /api/stakepoint (GET)
// Proxies StakePoint lock data server-side to avoid CORS restrictions.
// Tries multiple endpoint patterns to maximise compatibility.
//
// SECURITY NOTES (Security Audit v1.2):
//   - Mint address validated against allowlist — no arbitrary proxy
//   - Read-only: no write operations, no user data forwarded
//   - Result cached by Vercel edge for 30 seconds (Cache-Control)
//   - Input strictly validated

const ALLOWED_MINTS = {
  SPT: '6uUU2z5GBasaxnkcqiQVHa2SXL68mAXDsq1zYN5Qxrm7',
  YST: 'jYwmSavfx69a35JEkpyrxu9JUjvswEvfnhLCDV9vREV',
};

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', 'https://yakkstudios.xyz');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Cache-Control', 's-maxage=30, stale-while-revalidate=10');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') return res.status(405).json({ error: 'Method not allowed' });

  const { mint } = req.query;

  // Validate mint against allowlist — prevent open proxy abuse
  const allowed = Object.values(ALLOWED_MINTS);
  if (!mint || !allowed.includes(mint)) {
    return res.status(400).json({ error: 'Invalid or disallowed mint address' });
  }

  // Try StakePoint API endpoints in order of preference
  const endpoints = [
    `https://stakepoint.app/api/locks/${mint}`,
    `https://stakepoint.app/api/v1/locks?mint=${mint}`,
    `https://stakepoint.app/api/token/${mint}/locks`,
  ];

  let lastError = null;
  for (const url of endpoints) {
    try {
      const upstream = await fetch(url, {
        headers: {
          'Accept': 'application/json',
          'User-Agent': 'YAKK-Studios-Proxy/1.2',
        },
        signal: AbortSignal.timeout(8000),
      });

      if (upstream.ok) {
        const data = await upstream.json();
        return res.status(200).json(data);
      }
      lastError = `${upstream.status} from ${url}`;
    } catch (err) {
      lastError = err.message;
    }
  }

  // All endpoints failed — return structured error
  return res.status(502).json({
    error: 'StakePoint API unavailable',
    detail: lastError,
    fallback: true,
    locks: [],
    totalLocked: 0,
    lockCount: 0,
  });
}
