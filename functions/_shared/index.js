/**
 * Shared helpers for cTrader auth functions
 * AES-GCM-256, grant locks, Appwrite client factory, cTrader token exchange
 */

const crypto = require('crypto');
const { Client, TablesDB, ID, Query, Users, Permission, Role } = require('node-appwrite');

// ─── Crypto ─────────────────────────────────────────────────────────

function getEncKey() {
  const k = process.env.TOKEN_ENCRYPTION_KEY;
  if (!k || k.length < 32) throw new Error('TOKEN_ENCRYPTION_KEY missing or too short');
  return crypto.createHash('sha256').update(k).digest();
}

function encrypt(plaintext) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', getEncKey(), iv);
  const enc = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, enc]).toString('base64');
}

function decrypt(ciphertext) {
  const buf = Buffer.from(ciphertext, 'base64');
  const iv = buf.slice(0, 12);
  const tag = buf.slice(12, 28);
  const enc = buf.slice(28);
  const decipher = crypto.createDecipheriv('aes-256-gcm', getEncKey(), iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(enc), decipher.final()]).toString('utf8');
}

// ─── HMAC state signing ─────────────────────────────────────────────

function signState(userId, secret) {
  const payload = `${userId}:${Date.now()}`;
  const sig = crypto.createHmac('sha256', secret).update(payload).digest('hex');
  return Buffer.from(`${payload}:${sig}`).toString('base64url');
}

function verifyState(stateB64, secret, maxAgeMs = 600_000) {
  try {
    const raw = Buffer.from(stateB64, 'base64url').toString('utf8');
    const [userId, tsStr, sig] = raw.split(':');
    if (!userId || !tsStr || !sig) return null;
    const ts = parseInt(tsStr, 10);
    if (Date.now() - ts > maxAgeMs) return null;
    const expected = crypto.createHmac('sha256', secret).update(`${userId}:${tsStr}`).digest('hex');
    if (!crypto.timingSafeEqual(Buffer.from(sig, 'hex'), Buffer.from(expected, 'hex'))) return null;
    return userId;
  } catch {
    return null;
  }
}

// ─── Appwrite client ────────────────────────────────────────────────

function makeAdminClient() {
  const client = new Client()
    .setEndpoint(process.env.APPWRITE_ENDPOINT || 'https://sgp.cloud.appwrite.io/v1')
    .setProject(process.env.APPWRITE_PROJECT_ID)
    .setKey(process.env.APPWRITE_API_KEY);
  return client;
}

function makeAdminDb() {
  return new TablesDB(makeAdminClient());
}

function makeAdminUsers() {
  return new Users(makeAdminClient());
}

function rowData(row) {
  if (!row) return row;
  if (typeof row === 'object' && row.data !== undefined) return row.data;
  return row;
}

// ─── Service config lookup ──────────────────────────────────────────

async function getServiceConfig(db, key, fallbackEnv = null) {
  try {
    const result = await db.listRows({
      databaseId: process.env.CTRADER_AUTH_DATABASE_ID,
      tableId: 'service_config',
      queries: [Query.equal('config_key', key)],
    });
    const rows = result.rows || result.documents || [];
    if (rows.length > 0) {
      const row = rows[0].data || rows[0];
      const value = row.config_value || row.config_json;
      if (typeof value === 'string' && value.trim()) {
        return JSON.parse(value);
      }
      return value || null;
    }
  } catch (err) {
    console.error(`[getServiceConfig] ${key} lookup failed:`, err.message);
  }
  if (fallbackEnv && process.env[fallbackEnv]) {
    return typeof process.env[fallbackEnv] === 'string' && process.env[fallbackEnv].startsWith('{')
      ? JSON.parse(process.env[fallbackEnv])
      : process.env[fallbackEnv];
  }
  return null;
}

// ─── Grant locks (TablesDB row-level) ───────────────────────────────

function grantLockRowId(grantId) {
  // Appwrite row IDs are capped at 36 chars; grant_ids can exceed that.
  return crypto.createHash('sha256').update(grantId).digest('hex').slice(0, 32);
}

async function acquireGrantLock(db, grantId, lockContext = 'refresh', timeoutMs = 30000) {
  const start = Date.now();
  const lockRowId = grantLockRowId(grantId);
  const lockTimeoutMs = 300000; // 5 minutes lock TTL
  
  // Check for and clear stale locks
  try {
    const existing = await db.listRows({
      databaseId: process.env.CTRADER_AUTH_DATABASE_ID,
      tableId: 'grant_locks',
      queries: [Query.equal('$id', lockRowId)],
    });
    
    if (existing.rows.length > 0) {
      const lock = existing.rows[0];
      const lockedAt = new Date(lock.locked_at).getTime();
      const now = Date.now();
      
      // If lock is stale (older than 5 minutes), try to clear it
      if (now - lockedAt > lockTimeoutMs) {
        try {
          await db.deleteRow({
            databaseId: process.env.CTRADER_AUTH_DATABASE_ID,
            tableId: 'grant_locks',
            rowId: lockRowId,
          });
        } catch {
          // Couldn't clear stale lock, continue with normal acquisition
        }
      }
    }
  } catch {
    // Ignore errors checking for existing lock
  }

  while (Date.now() - start < timeoutMs) {
    const now = new Date();
    const expiresAt = new Date(now.getTime() + lockTimeoutMs).toISOString();
    
    try {
      await db.createRow({
        databaseId: process.env.CTRADER_AUTH_DATABASE_ID,
        tableId: 'grant_locks',
        rowId: lockRowId,
        data: {
          locked_at: now.toISOString(),
          locked_by: lockContext,
          expires_at: expiresAt,
        },
      });
      return true;
    } catch (e) {
      if (e.code === 409) {
        await sleep(500);
        continue;
      }
      throw e;
    }
  }
  return false;
}

async function releaseGrantLock(db, grantId) {
  try {
    await db.deleteRow({
      databaseId: process.env.CTRADER_AUTH_DATABASE_ID,
      tableId: 'grant_locks',
      rowId: grantLockRowId(grantId),
    });
  } catch {
    // ignore not-found or race
  }
}

// ─── cTrader token exchange ─────────────────────────────────────────

async function exchangeCtraderCode(code, oauth = null) {
  const clientId = oauth?.clientId || process.env.CTRADER_CLIENT_ID;
  const clientSecret = oauth?.clientSecret || process.env.CTRADER_CLIENT_SECRET;
  const redirectUri = oauth?.redirectUri || process.env.CTRADER_REDIRECT_URI;
  const params = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: clientId,
    client_secret: clientSecret,
    code,
    redirect_uri: redirectUri,
  });

  const res = await fetch(`https://openapi.ctrader.com/apps/token?${params}`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`cTrader token exchange failed: ${res.status} ${text}`);
    err.status = res.status;
    throw err;
  }

  const data = await res.json();
  if (data.errorCode) {
    const err = new Error(`cTrader token exchange failed: ${data.errorCode} - ${data.description || ''}`);
    err.status = 400;
    throw err;
  }

  return {
    access_token: data.accessToken,
    refresh_token: data.refreshToken,
    expires_in: data.expiresIn,
  };
}

async function refreshCtraderToken(refreshToken, oauth = null) {
  const clientId = oauth?.clientId || process.env.CTRADER_CLIENT_ID;
  const clientSecret = oauth?.clientSecret || process.env.CTRADER_CLIENT_SECRET;
  const params = new URLSearchParams({
    grant_type: 'refresh_token',
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: refreshToken,
  });

  const res = await fetch(`https://openapi.ctrader.com/apps/token?${params}`, {
    method: 'POST',
    headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`cTrader refresh failed: ${res.status} ${text}`);
    err.status = res.status;
    throw err;
  }

  const data = await res.json();
  if (data.errorCode) {
    const err = new Error(`cTrader refresh failed: ${data.errorCode} - ${data.description || ''}`);
    err.status = 400;
    throw err;
  }

  return {
    access_token: data.accessToken,
    refresh_token: data.refreshToken,
    expires_in: data.expiresIn,
  };
}

// ─── Helpers ────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function jsonResponse(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
}

function redirectResponse(url, status = 302) {
  return new Response(null, { status, headers: { Location: url } });
}

function generateGrantId() {
  // Keep total length ≤ 36 chars so it can safely be used as a TablesDB rowId.
  return 'grant_' + crypto.randomBytes(14).toString('hex');
}

function generateToken() {
  return crypto.randomBytes(32).toString('hex');
}

// ─── CORS + Cookie helpers ──────────────────────────────────────────

const CORS_ORIGINS = (process.env.SITE_URL || process.env.SITES_URL || 'https://app.mrme.tech')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);
const COOKIE_DOMAIN = process.env.COOKIE_DOMAIN || '.mrme.tech';

function corsHeaders(origin) {
  const allowed = origin && CORS_ORIGINS.includes(origin)
    ? origin
    : CORS_ORIGINS[0] || 'https://app.mrme.tech';
  return {
    'Access-Control-Allow-Origin': allowed,
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, x-internal-key',
    'Access-Control-Max-Age': '86400',
  };
}

function handleOptions(req, res) {
  if (req.method === 'OPTIONS') {
    const origin = req.headers['origin'] || '';
    return res.send('', 204, corsHeaders(origin));
  }
  return null;
}

function sessionCookie(name, secret, maxAge = 604800) {
  return `${name}=${secret}; HttpOnly; Secure; SameSite=Lax; Domain=${COOKIE_DOMAIN}; Path=/; Max-Age=${maxAge}`;
}

function clearCookie(name) {
  return `${name}=; HttpOnly; Secure; SameSite=Lax; Domain=${COOKIE_DOMAIN}; Path=/; Max-Age=0`;
}

// ─── Exports ────────────────────────────────────────────────────────

module.exports = {
  encrypt,
  decrypt,
  signState,
  verifyState,
  makeAdminClient,
  makeAdminDb,
  makeAdminUsers,
  rowData,
  getServiceConfig,
  acquireGrantLock,
  releaseGrantLock,
  exchangeCtraderCode,
  refreshCtraderToken,
  sleep,
  jsonResponse,
  redirectResponse,
  generateGrantId,
  generateToken,
  corsHeaders,
  handleOptions,
  sessionCookie,
  clearCookie,
  CORS_ORIGIN: CORS_ORIGINS[0] || 'https://app.mrme.tech',
  COOKIE_DOMAIN,
  ID,
  Query,
  Permission,
  Role,
};
