/**
 * ctrader-internal — server-to-server for Python backends
 * POST /internal/ctrader/refresh
 * GET /internal/grant/latest
 * POST /internal/grant/:grant_id/accounts
 * Gated by x-internal-key header
 */

const crypto = require('crypto');

const {
  makeAdminClient,
  makeAdminDb,
  decrypt,
  encrypt,
  acquireGrantLock,
  releaseGrantLock,
  refreshCtraderToken,
  corsHeaders,
  handleOptions,
  Query,
} = require('./_shared');

const DB_ID = process.env.CTRADER_AUTH_DATABASE_ID;
const INTERNAL_KEY = process.env.INTERNAL_API_KEY;

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
    if (!checkInternalKey(req)) {
      return res.json({ error: 'Unauthorized' }, 401, corsHeaders());
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

    return res.json({ error: 'Not found' }, 404, corsHeaders());
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Internal error', detail: err.message }, 500, corsHeaders());
  }
};

// ─── POST /internal/ctrader/refresh ─────────────────────────────────

async function handleRefresh(req, res, log, error) {
  const body = req.bodyJson || {};
  const grantId = String(body.grantId || '');

  if (!grantId) {
    return res.json({ error: 'grantId required' }, 400, corsHeaders());
  }

  const db = makeAdminDb();
  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    queries: [Query.equal('grant_id', grantId)],
  });

  if (list.rows.length === 0) {
    return res.json({ error: 'Grant not found' }, 404, corsHeaders());
  }

  const slave = list.rows[0];

  if (slave.status !== 'active') {
    return res.json({ error: `Grant status is ${slave.status}` }, 403, corsHeaders());
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
      }, 200, corsHeaders());
    } catch {
      // decryption failed, fall through to refresh
    }
  }

  if (!slave.refresh_token_enc) {
    return res.json({ error: 'No refresh token available' }, 403, corsHeaders());
  }

  const acquired = await acquireGrantLock(db, grantId, 'refresh', 30000);
  if (!acquired) {
    return res.json({ error: 'Grant locked by another refresh' }, 423, corsHeaders());
  }

  try {
    const refreshToken = decrypt(slave.refresh_token_enc);
    const tokenData = await refreshCtraderToken(refreshToken);
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
    }, 200, corsHeaders());
  } catch (err) {
    error(`Refresh failed for ${grantId}: ${err.message}`);
    if (err.status === 400 || err.status === 401) {
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'slave_accounts',
        rowId: slave.$id,
        data: { status: 'reauth_required' },
      });
      return res.json({ error: 'Refresh token invalid, re-authentication required' }, 401, corsHeaders());
    }
    return res.json({ error: 'cTrader refresh failed', detail: err.message }, 502, corsHeaders());
  } finally {
    await releaseGrantLock(db, grantId);
  }
}

// ─── GET /internal/grant/latest ─────────────────────────────────────

async function handleGrantLatest(req, res, log, error) {
  const userId = String(req.query.user_id || '');
  if (!userId) {
    return res.json({ error: 'user_id required' }, 400, corsHeaders());
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
    return res.json({ grant_id: null }, 200, corsHeaders());
  }

  return res.json({
    grant_id: list.rows[0].grant_id,
    updated_at: list.rows[0].$updatedAt,
  }, 200, corsHeaders());
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

async function handleGrantAccounts(req, res, log, error) {
  const parts = req.path.split('/');
  const grantId = parts[3];

  if (!grantId) {
    return res.json({ error: 'grant_id required in path' }, 400, corsHeaders());
  }

  const body = req.bodyJson || {};
  const db = makeAdminDb();

  const slaveList = await db.listRows({
    databaseId: DB_ID,
    tableId: 'slave_accounts',
    queries: [Query.equal('grant_id', grantId)],
  });
  if (slaveList.rows.length === 0) {
    return res.json({ error: 'Grant not found' }, 404, corsHeaders());
  }
  const slave = slaveList.rows[0];

  // Rich per-account payload from ctrader-open-api
  const richAccounts = Array.isArray(body.accounts) ? body.accounts : [];
  // Legacy flat-array fallback
  const legacyIds = Array.isArray(body.accountIds) ? body.accountIds : [];
  const selectedAccountId = String(body.selectedAccountId || '');

  const accountRows = [];
  const accountIds = [];

  if (richAccounts.length > 0) {
    for (const acc of richAccounts) {
      const id = String(acc.ctidTraderAccountId || '');
      if (!id) continue;
      accountIds.push(id);
      const rowId = accountRowId(grantId, id);
      const row = {
        grant_id: grantId,
        ctidTraderAccountId: id,
        isLive: acc.isLive === true || acc.isLive === 'true',
        traderLogin: acc.traderLogin ? String(acc.traderLogin) : '',
        brokerTitleShort: acc.brokerTitleShort ? String(acc.brokerTitleShort) : '',
        lastClosingDealTimestamp: acc.lastClosingDealTimestamp ? new Date(Number(acc.lastClosingDealTimestamp)).toISOString() : null,
        lastBalanceUpdateTimestamp: acc.lastBalanceUpdateTimestamp ? new Date(Number(acc.lastBalanceUpdateTimestamp)).toISOString() : null,
        selected: (selectedAccountId && selectedAccountId === id) || acc.selected === true || acc.selected === 'true',
      };
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
    for (const acc of existingAccounts.rows || []) {
      const isSelected = String(acc.ctidTraderAccountId) === selectedAccountId;
      if (acc.selected !== isSelected) {
        await db.updateRow({
          databaseId: DB_ID,
          tableId: 'accounts',
          rowId: acc.$id,
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
  return res.json({ success: true, grant_id: grantId, accounts: accountIds.length, detail: accountRows }, 200, corsHeaders());
}

// ─── GET /internal/grant/:grant_id/accounts ─────────────────────────

async function handleGetAccounts(req, res, log, error) {
  const parts = req.path.split('/');
  const grantId = parts[3];

  if (!grantId) {
    return res.json({ error: 'grant_id required in path' }, 400, corsHeaders());
  }

  const db = makeAdminDb();
  const list = await db.listRows({
    databaseId: DB_ID,
    tableId: 'accounts',
    queries: [Query.equal('grant_id', grantId)],
  });

  const accounts = (list.rows || []).map((acc) => ({
    ctidTraderAccountId: acc.ctidTraderAccountId,
    isLive: acc.isLive,
    traderLogin: acc.traderLogin,
    brokerTitleShort: acc.brokerTitleShort,
    lastClosingDealTimestamp: acc.lastClosingDealTimestamp,
    lastBalanceUpdateTimestamp: acc.lastBalanceUpdateTimestamp,
    selected: acc.selected,
  }));

  return res.json({ success: true, grant_id: grantId, accounts }, 200, corsHeaders());
}
