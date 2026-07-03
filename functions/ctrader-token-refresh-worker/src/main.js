/**
 * ctrader-token-refresh-worker — Scheduled cron + on-demand HTTP
 * Rotates near-expiry active grants and sweeps stale ephemeral tokens
 */

const {
  makeAdminDb,
  getServiceConfig,
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
const BUFFER_HOURS = parseFloat(process.env.REFRESH_BUFFER_HOURS || '48');

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

module.exports = async function main({ req, res, log, error }) {
  const preflight = handleOptions(req, res);
  if (preflight) return preflight;

  try {
    if (req.method === 'GET' && req.path === '/health') {
      return res.json({ status: 'ok', service: 'ctrader-token-refresh-worker' }, 200, corsHeaders(req.headers['origin'] || ''));
    }

    const results = {
      rotated: 0,
      failed: 0,
      skipped: 0,
      swept: 0,
      errors: [],
    };

    const db = makeAdminDb();
    const now = new Date();
    const expiryThreshold = new Date(now.getTime() + BUFFER_HOURS * 60 * 60 * 1000).toISOString();

    // Find active grants expiring soon (process in batches of 100)
    let allExpiring = [];
    let offset = 0;
    const batchSize = 100;
    
    while (true) {
      const batch = await db.listRows({
        databaseId: DB_ID,
        tableId: 'slave_accounts',
        queries: [
          Query.equal('status', 'active'),
          Query.lessThan('access_token_expires_at', expiryThreshold),
          Query.limit(batchSize),
          Query.offset(offset),
        ],
      });
      
      if (batch.rows.length === 0) break;
      allExpiring = allExpiring.concat(batch.rows);
      offset += batchSize;
      
      // Stop if we've processed a reasonable number (1000 max)
      if (offset >= 1000) {
        log(`Warning: Reached max batch limit of ${offset} grants`);
        break;
      }
    }

    log(`Found ${allExpiring.length} grants needing refresh`);
    const oauth = await getOAuthConfig();

    for (const slave of allExpiring) {
      const rotated = await rotateOne(db, slave, oauth, log, error);
      if (rotated === true) results.rotated++;
      else if (rotated === false) results.failed++;
      else results.skipped++;
    }

    // Sweep old ephemeral tokens (process in batches)
    const staleTokenCutoff = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString();
    try {
      let allStale = [];
      let tokenOffset = 0;
      const tokenBatchSize = 100;
      
      while (true) {
        const batch = await db.listRows({
          databaseId: DB_ID,
          tableId: 'ephemeral_tokens',
          queries: [
            Query.lessThan('expires_at', staleTokenCutoff),
            Query.limit(tokenBatchSize),
            Query.offset(tokenOffset),
          ],
        });
        
        if (batch.rows.length === 0) break;
        allStale = allStale.concat(batch.rows);
        tokenOffset += tokenBatchSize;
        
        // Stop if we've processed a reasonable number (1000 max)
        if (tokenOffset >= 1000) {
          log(`Warning: Reached max token sweep limit of ${tokenOffset}`);
          break;
        }
      }
      
      for (const token of allStale) {
        try {
          await db.deleteRow({
            databaseId: DB_ID,
            tableId: 'ephemeral_tokens',
            rowId: token.$id,
          });
          results.swept++;
        } catch (e) {
          // ignore individual delete errors
        }
      }
      log(`Swept ${results.swept} stale ephemeral tokens`);
    } catch (e) {
      error(`Token sweep failed: ${e.message}`);
    }

    return res.json({
      success: true,
      processed: expiringList.rows.length,
      ...results,
    }, 200, corsHeaders());
  } catch (err) {
    error(String(err));
    return res.json({ error: 'Worker failed', detail: err.message }, 500, corsHeaders());
  }
};

async function rotateOne(db, slave, oauth, log, error) {
  const grantId = slave.grant_id;

  if (!slave.refresh_token_enc) {
    log(`Skip ${grantId}: no refresh token`);
    return null; // skipped
  }

  const acquired = await acquireGrantLock(db, grantId, 'cron', 30000);
  if (!acquired) {
    log(`Skip ${grantId}: could not acquire lock`);
    return null; // skipped
  }

  try {
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

    log(`Rotated grant=${grantId} new_expires=${newExpiresAt}`);
    return true;
  } catch (err) {
    error(`Rotation failed for ${grantId}: ${err.message}`);
    if (err.status === 400 || err.status === 401) {
      await db.updateRow({
        databaseId: DB_ID,
        tableId: 'slave_accounts',
        rowId: slave.$id,
        data: { status: 'reauth_required' },
      });
    }
    return false;
  } finally {
    await releaseGrantLock(db, grantId);
  }
}
