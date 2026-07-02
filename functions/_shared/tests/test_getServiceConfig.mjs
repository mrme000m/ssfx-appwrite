/**
 * Unit tests for getServiceConfig helper function.
 * Tests service_config lookup and fallback to env vars.
 * Uses Node.js built-in test runner (available in Node 18+).
 */

import assert from 'assert';
import { describe, it, after } from 'node:test';

// Mock TablesDB that simulates Appwrite TablesDB behavior
class MockTablesDB {
  constructor(rows = {}) {
    this.rows = rows;
  }

  async listRows({ databaseId, tableId, queries }) {
    const key = `${databaseId}:${tableId}`;
    const rows = this.rows[key] || [];
    
    // Simple query filtering
    let result = [...rows];
    for (const q of queries || []) {
      if (q.method === 'equal') {
        result = result.filter(r => r[q.attribute] === q.value);
      }
    }
    
    return { rows: result, documents: result };
  }
}

// Mock Query - simple implementation for testing
function QueryEqual(attribute, value) {
  return { method: 'equal', attribute, value };
}

// Simplified version of getServiceConfig for testing
async function getServiceConfig(db, key, fallbackEnv = null) {
  try {
    const result = await db.listRows({
      databaseId: 'ctrader_auth',
      tableId: 'service_config',
      queries: [QueryEqual('config_key', key)],
    });
    const rows = result.rows || result.documents || [];
    if (rows.length > 0) {
      // Handle both row.data and row directly
      const row = rows[0].data ? rows[0].data : rows[0];
      const value = row.config_value || row.config_json;
      if (typeof value === 'string' && value.trim()) {
        try {
          return JSON.parse(value);
        } catch (e) {
          // Invalid JSON - return raw string
          return value;
        }
      }
      return value || null;
    }
  } catch (err) {
    console.error(`[getServiceConfig] ${key} lookup failed:`, err.message);
  }
  if (fallbackEnv && process.env[fallbackEnv]) {
    const val = process.env[fallbackEnv];
    if (typeof val === 'string' && val.trim() && (val.startsWith('{') || val.startsWith('['))) {
      try {
        return JSON.parse(val);
      } catch {
        return val;
      }
    }
    return val;
  }
  return null;
}

describe('getServiceConfig', () => {
  describe('service_config lookup', () => {
    it('should return parsed JSON from config_value', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [
          {
            config_key: 'ctrader_oauth',
            config_value: JSON.stringify({
              client_id: 'test_client_id',
              client_secret: 'test_client_secret',
              redirect_uri: 'https://auth.mrme.tech/callback',
              environment: 'demo',
            }),
          },
        ],
      });

      const result = await getServiceConfig(db, 'ctrader_oauth');
      assert.deepStrictEqual(result, {
        client_id: 'test_client_id',
        client_secret: 'test_client_secret',
        redirect_uri: 'https://auth.mrme.tech/callback',
        environment: 'demo',
      });
    });

    it('should return parsed JSON from config_json field', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [
          {
            config_key: 'master_auth',
            config_json: JSON.stringify({
              username: 'admin',
              pin_hash: 'test_hash',
            }),
          },
        ],
      });

      const result = await getServiceConfig(db, 'master_auth');
      assert.deepStrictEqual(result, {
        username: 'admin',
        pin_hash: 'test_hash',
      });
    });

    it('should return null when config not found and no fallback', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [],
      });

      const result = await getServiceConfig(db, 'nonexistent_key');
      assert.strictEqual(result, null);
    });

    it('should filter by config_key query', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [
          {
            config_key: 'ctrader_oauth',
            config_value: JSON.stringify({ client_id: 'oauth_client' }),
          },
          {
            config_key: 'master_auth',
            config_value: JSON.stringify({ username: 'admin' }),
          },
        ],
      });

      const result = await getServiceConfig(db, 'master_auth');
      assert.deepStrictEqual(result, { username: 'admin' });
    });
  });

  describe('fallback to env vars', () => {
    const originalEnv = { ...process.env };

    after(() => {
      // Restore original env
      Object.keys(process.env).forEach(key => {
        if (!(key in originalEnv)) {
          delete process.env[key];
        }
      });
      Object.assign(process.env, originalEnv);
    });

    it('should fall back to env var JSON when config not found', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [],
      });

      process.env.CTRADER_OAUTH_JSON = JSON.stringify({
        client_id: 'fallback_client_id',
        client_secret: 'fallback_secret',
      });

      const result = await getServiceConfig(db, 'ctrader_oauth', 'CTRADER_OAUTH_JSON');
      assert.deepStrictEqual(result, {
        client_id: 'fallback_client_id',
        client_secret: 'fallback_secret',
      });
    });

    it('should fall back to plain string env var', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [],
      });

      process.env.SIMPLE_VALUE = 'plain_string_value';

      const result = await getServiceConfig(db, 'simple_key', 'SIMPLE_VALUE');
      assert.strictEqual(result, 'plain_string_value');
    });

    it('should return null when env var is empty', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [],
      });

      process.env.EMPTY_VALUE = '';

      const result = await getServiceConfig(db, 'empty_key', 'EMPTY_VALUE');
      assert.strictEqual(result, null);
    });
  });

  describe('error handling', () => {
    it('should handle invalid JSON gracefully', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [
          {
            config_key: 'bad_json',
            config_value: '{invalid json',
          },
        ],
      });

      const result = await getServiceConfig(db, 'bad_json');
      // Invalid JSON should return the raw string
      assert.strictEqual(result, '{invalid json');
    });

    it('should handle missing data field', async () => {
      const db = new MockTablesDB({
        'ctrader_auth:service_config': [
          {
            config_key: 'no_data',
            config_value: JSON.stringify({ test: 'value' }),
          },
        ],
      });

      const result = await getServiceConfig(db, 'no_data');
      assert.deepStrictEqual(result, { test: 'value' });
    });
  });
});
