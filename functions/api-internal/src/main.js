/**
 * api-internal — server-to-server for Python backends
 * POST /api/internal/token/refresh
 * GET /api/internal/grant/latest
 * POST /api/internal/grant/:grant_id/accounts
 * Gated by x-internal-key header
 */

const crypto = require('crypto');

const {
  makeAdminClient,
  makeAdminDb,
  getServiceConfig,
  decrypt,
  encrypt,
  acquireGrantLock,
  releaseGrantLock,
  refreshCtraderToken,
  corsHeaders,
  handleOptions,
  rowData,
  Query,
} = require('./_shared');

const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;
const INTERNAL_KEY = process.env.INTERNAL_API_KEY;

async function getOAuthConfig() {
  const db = makeAdminDb();
  const svc = await getServiceConfig(db, 'ctrader_oauth', 'CTRADER_OAUTH_JSON');
  if (svc && typeof svc === 'object' && svc.client_id) {
    return {
      clientId: svc.client_id,
      clientSecret: svc.client_secret,
      redirectUri: svc.redirect_uri,
      environment: svc.environment,
    };
  }
  return {
    clientId: process.env.CTRADER_CLIENT_ID,
    clientSecret: process.env.CTRADER_CLIENT_SECRET,
    redirectUri: process.env.CTRADER_REDIRECT_URI,
    environment: process.env.CTRADER_ENVIRONMENT || 'demo',
  };
}

function checkInternalKey(req) {
  const key = req.headers['x-internal-key'] || '';
  return key === INTERNAL_KEY;
}

function accountRowId(grantId, accountId) {
  return crypto.createHash('sha256').update(`${grantId}:${accountId}`).digest('hex').slice(0, 32);
}

module.exports = async function main({ req, res, log, error }) {
  const preflight = handleOptions(req, res);
  if (preflight) return preflight;

  const path = req.path;
  const method = req.method;

  try {
    if (path === '/health' && method === 'GET') {
      return res.json({ status: 'ok', service: 'api-internal' }, 200, corsHeaders(req.headers['origin'] || ''));
    }
    if (!checkInternalKey(req)) {
      return res.json({ error: 'Unauthorized' }, 401, corsHeaders(req.headers['origin'] || ''));
    }

    if (path === '/internal/ctrader/refresh' && method === 'POST') {
      return await handleRefresh(req, res, log, error);
    }
    if (path === '/internal/grant/latest' && method === 'GET') {
      return await handleGrantLatest(req, res, log, error);
    }
    if (path.startsWith('/internal/grant/') && path.endsWith('/accounts')) {
      if (method === 'POST') return await handleGrantAccounts(req, res, log, error);
      if (method === 'GET') return await handleGetAccounts(req, res, log, error);
    }

    return res.json({ error: 'Not found' }, 404, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500, corsHeaders(req.headers['origin'] || ''));
  }
};

// ─── POST /internal/ctrader/refresh ─────────────────────────────────

async function handleRefresh(req, res, log, error) {
  const body = req.bodyJson || {};
  const grantId = String(body.grantId || '');

  if (!grantId) {
    return res.json({ error: 'grantId required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();
  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    queries: [Query.equal('grant_id', grantId)],
  });

  if (list.rows.length === 0) {
    return res.json({ error: 'Grant not found' }, 404, corsHeaders(req.headers['origin'] || ''));
  }

  const slave = list.rows[0];

  if (slave.status !== 'active') {
    return res.json({ error: `Grant status is ${slave.status}` }, 403, corsHeaders(req.headers['origin'] || ''));
  }

  const now = Date.now();
  const expiresAt = slave.access_token_expires_at
    ? new Date(slave.access_token_expires_at).getTime()
    : 0;

  if (expiresAt > now + 5 * 60 * 1000 && slave.access_token_enc) {
    try {
      const accessToken = decrypt(slave.access_token_enc);
      return res.json({
        access_token: accessToken,
        expires_at: slave.access_token_expires_at,
        refreshed: false,
      }, 200, corsHeaders(req.headers['origin'] || ''));
    } catch {
      // decryption failed, fall through to refresh
    }
  }

  if (!slave.refresh_token_enc) {
    return res.json({ error: 'No refresh token available' }, 403, corsHeaders(req.headers['origin'] || ''));
  }

  const acquired = await acquireGrantLock(db, grantId, 'refresh', 30000);
  if (!acquired) {
    return res.json({ error: 'Grant locked by another refresh' }, 423, corsHeaders(req.headers['origin'] || ''));
  }

  try {
    const oauth = await getOAuthConfig();
    const refreshToken = decrypt(slave.refresh_token_enc);
    const tokenData = await refreshCtraderToken(refreshToken, oauth);
    const { access_token, refresh_token, expires_in } = tokenData;
    const newExpiresAt = new Date(Date.now() + (expires_in || 3600) * 1000).toISOString();

    const newAccessEnc = encrypt(access_token);
    const newRefreshEnc = refresh_token ? encrypt(refresh_token) : slave.refresh_token_enc;

    await db.updateRow({
      databaseId: DB_ID,
      tableId: 'slave_accounts',
      rowId: slave.$id,
      data: {
        access_token_enc: newAccessEnc,
        refresh_token_enc: newRefreshEnc,
        access_token_expires_at: newExpiresAt,
      },
    });

    log(`Refreshed grant=${grantId} new_expires=${newExpiresAt}`);

    return res.json({
      access_token: access_token,
      expires_at: newExpiresAt,
      refreshed: true,
    }, 200, corsHeaders(req.headers['origin'] || ''));
  } catch (err) {
    error(`Refresh failed for ${grantId}: ${err.message}`);
    if (err.status === 400 || err.status === 401) {
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'slave_accounts',
        rowId: slave.$id,
        data: { status: 'reauth_required' },
      });
      return res.json({ error: 'Refresh token invalid, re-authentication required' }, 401, corsHeaders(req.headers['origin'] || ''));
    }
    return res.json({ error: 'cTrader refresh failed', detail: err.message }, 502, corsHeaders(req.headers['origin'] || ''));
  } finally {
    await releaseGrantLock(db, grantId);
  }
}

// ─── GET /internal/grant/latest ─────────────────────────────────────

async function handleGrantLatest(req, res, log, error) {
  const userId = String(req.query.user_id || '');
  if (!userId) {
    return res.json({ error: 'user_id required' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();
  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    queries: [
      Query.equal('appwrite_user_id', userId),
      Query.equal('status', 'active'),
      Query.orderDesc('$updatedAt'),
      Query.limit(1),
    ],
  });

  if (list.rows.length === 0) {
    return res.json({ grant_id: null }, 200, corsHeaders(req.headers['origin'] || ''));
  }

  return res.json({
    grant_id: list.rows[0].grant_id,
    updated_at: list.rows[0].$updatedAt,
  }, 200, corsHeaders(req.headers['origin'] || ''));
}

// ─── POST /internal/grant/:grant_id/accounts ────────────────────────

async function upsertAccountRow(db, rowId, data) {
  try {
    await db.createRow({
      databaseId: DB_ID,
      tableId: 'accounts',
      rowId,
      data,
    });
  } catch (e) {
    if (e.code === 409) {
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'accounts',
        rowId,
        data,
      });
    } else {
      throw e;
    }
  }
}

function normalizeAccount(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const idRaw = raw.ctidTraderAccountId !== undefined ? raw.ctidTraderAccountId : raw.accountId;
  const id = Number(idRaw);
  if (!Number.isInteger(id) || id <= 0) return null;

  const moneyDigits = typeof raw.moneyDigits === 'number' ? raw.moneyDigits : 0;
  const rawBalance = typeof raw.balance === 'number' ? raw.balance : 0;
  const divisor = moneyDigits > 0 ? 10 ** moneyDigits : 100;

  const leverageInCentsRaw = raw.leverageInCents !== undefined ? raw.leverageInCents : raw.leverage;
  const leverageInCents = typeof leverageInCentsRaw === 'number'
    ? leverageInCentsRaw
    : (typeof leverageInCentsRaw === 'string' ? parseInt(leverageInCentsRaw, 10) || 0 : 0);
  // If we only got a plain leverage multiplier (e.g. 200), convert to cents (20000).
  const normalizedLeverageInCents = leverageInCents < 1000 ? leverageInCents * 100 : leverageInCents;

  const ts = (v) => {
    if (!v && v !== 0) return null;
    const n = Number(v);
    if (Number.isNaN(n)) return null;
    return new Date(n).toISOString();
  };

  return {
    grant_id: raw.grant_id,
    ctidTraderAccountId: id,
    isLive: raw.isLive === true || raw.isLive === 'true' || raw.live === true || raw.live === 'true',
    traderLogin: String(raw.traderLogin || raw.accountNumber || ''),
    brokerTitleShort: String(raw.brokerTitleShort || raw.brokerTitle || ''),
    brokerName: String(raw.brokerName || ''),
    lastClosingDealTimestamp: ts(raw.lastClosingDealTimestamp),
    lastBalanceUpdateTimestamp: ts(raw.lastBalanceUpdateTimestamp),
    balance: rawBalance / divisor,
    moneyDigits,
    accountType: String(raw.accountType || raw.traderAccountType || ''),
    depositAssetId: String(raw.depositAssetId || raw.depositCurrency || ''),
    leverageInCents: normalizedLeverageInCents,
    registrationTimestamp: ts(raw.registrationTimestamp || raw.traderRegistrationTimestamp),
    selected: raw.selected === true || raw.selected === 'true',
  };
}

async function handleGrantAccounts(req, res, log, error) {
  const parts = req.path.split('/');
  const grantId = parts[3];

  if (!grantId) {
    return res.json({ error: 'grant_id required in path' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const body = req.bodyJson || {};
  const db = makeAdminDb();

  const slaveList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    queries: [Query.equal('grant_id', grantId)],
  });
  if (slaveList.rows.length === 0) {
    return res.json({ error: 'Grant not found' }, 404, corsHeaders(req.headers['origin'] || ''));
  }
  const slave = rowData(slaveList.rows[0]) || slaveList.rows[0];

  // Rich per-account payload from ctrader-open-api or REST snapshot.
  // Accept either { accounts: [...] } or { data: [...] }.
  const richAccounts = Array.isArray(body.accounts) ? body.accounts : (Array.isArray(body.data) ? body.data : []);
  // Legacy flat-array fallback
  const legacyIds = Array.isArray(body.accountIds) ? body.accountIds : [];
  const selectedAccountId = String(body.selectedAccountId || '');

  const accountRows = [];
  const accountIds = [];

  if (richAccounts.length > 0) {
    for (const acc of richAccounts) {
      const row = normalizeAccount({ ...acc, grant_id: grantId });
      if (!row) continue;
      const id = row.ctidTraderAccountId;
      accountIds.push(id);
      const rowId = accountRowId(grantId, id);
      row.selected = (selectedAccountId && selectedAccountId === id) || row.selected;
      accountRows.push(row);
      try {
        await upsertAccountRow(db, rowId, row);
      } catch (e) {
        error(`Account upsert failed for ${rowId}: ${e.message} (code=${e.code})`);
      }
    }
  } else if (legacyIds.length > 0) {
    for (const id of legacyIds) {
      const sid = String(id);
      accountIds.push(sid);
      const rowId = accountRowId(grantId, sid);
      const row = {
        grant_id: grantId,
        ctidTraderAccountId: sid,
        selected: selectedAccountId === sid,
      };
      accountRows.push(row);
      try {
        await upsertAccountRow(db, rowId, row);
      } catch (e) {
        error(`Account upsert failed for ${rowId}: ${e.message} (code=${e.code})`);
      }
    }
  }

  // Clear previously selected if changed
  if (selectedAccountId && accountIds.length > 0) {
    const existingAccounts = await db.listRows({
      databaseId: DB_ID,
      tableId: 'accounts',
      queries: [Query.equal('grant_id', grantId)],
    });
    for (const row of existingAccounts.rows || []) {
      const acc = rowData(row) || row;
      const isSelected = String(acc.ctidTraderAccountId) === selectedAccountId;
      const rowId = row.$id || acc.$id;
      if (acc.selected !== isSelected && rowId) {
        await db.updateRow({
          databaseId: DB_ID,
          tableId: 'accounts',
          rowId,
          data: { selected: isSelected },
        });
      }
    }
  }

  const updates = {
    ctrader_account_ids: accountIds.join(','),
    last_heartbeat_at: new Date().toISOString(),
  };
  if (selectedAccountId) {
    updates.selected_account_id = selectedAccountId;
  }

  await db.updateRow({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    rowId: slave.$id,
    data: updates,
  });

  log(`Updated accounts grant=${grantId} accounts=${accountIds.length}`);
  return res.json({ success: true, grant_id: grantId, accounts: accountIds.length, detail: accountRows }, 200, corsHeaders(req.headers['origin'] || ''));
}

// ─── GET /internal/grant/:grant_id/accounts ─────────────────────────

async function handleGetAccounts(req, res, log, error) {
  const parts = req.path.split('/');
  const grantId = parts[3];

  if (!grantId) {
    return res.json({ error: 'grant_id required in path' }, 400, corsHeaders(req.headers['origin'] || ''));
  }

  const db = makeAdminDb();
  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'accounts',
    queries: [Query.equal('grant_id', grantId)],
  });

  const accounts = (list.rows || []).map((row) => {
    const acc = rowData(row) || row;
    return {
      ctidTraderAccountId: acc.ctidTraderAccountId,
      isLive: acc.isLive,
      traderLogin: acc.traderLogin,
      brokerTitleShort: acc.brokerTitleShort,
      brokerName: acc.brokerName,
      lastClosingDealTimestamp: acc.lastClosingDealTimestamp,
      lastBalanceUpdateTimestamp: acc.lastBalanceUpdateTimestamp,
      balance: acc.balance,
      moneyDigits: acc.moneyDigits,
      accountType: acc.accountType,
      depositAssetId: acc.depositAssetId,
      leverageInCents: acc.leverageInCents,
      registrationTimestamp: acc.registrationTimestamp,
      selected: acc.selected,
    };
  });

  return res.json({ success: true, grant_id: grantId, accounts }, 200, corsHeaders(req.headers['origin'] || ''));
}
