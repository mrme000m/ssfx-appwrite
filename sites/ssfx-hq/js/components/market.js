/**
 * SSFX HQ — Market data / gold quant dashboard (master only).
 */
window.MarketComponent = (function () {
  function renderLevel(level) {
    return `<div class="flex justify-between text-sm">
      <span class="text-dim">${window.UI.esc(level.label || '')}</span>
      <span class="mono">${window.UI.formatPrice(level.price)}</span>
    </div>`;
  }

  function renderQuant(container, data) {
    const mtf = data.mtf || {};
    const orderFlow = data.order_flow || {};
    const keyLevels = data.key_levels || {};
    const decision = data.decision || {};

    container.innerHTML = `
      <div class="page-header">
        <h1>Market <span class="page-subtitle">${window.UI.esc(data.symbol || 'XAUUSD')} gold quant</span></h1>
      </div>
      <div class="data-grid data-grid-4">
        <div class="stat-card">
          <div class="stat-label">Bid</div>
          <div class="stat-value">${window.UI.formatPrice(data.bid)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Ask</div>
          <div class="stat-value">${window.UI.formatPrice(data.ask)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Spread</div>
          <div class="stat-value">${window.UI.formatPrice(data.spread, 2)}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">MTF Bias</div>
          <div class="stat-value" style="font-size:1.1rem">${window.UI.esc(mtf.overall_direction || '—')}</div>
        </div>
      </div>

      <div class="data-grid data-grid-3 mt-6">
        <div class="card">
          <h3 class="mb-4">Multi-Timeframe</h3>
          ${(mtf.readings || []).map((r) => `
            <div class="mb-2">
              <div class="flex justify-between">
                <span class="mono text-sm">${window.UI.esc(r.timeframe)}</span>
                <span class="badge ${r.direction === 'bullish' ? 'badge-teal' : r.direction === 'bearish' ? 'badge-red' : 'badge-amber'}">${window.UI.esc(r.direction)}</span>
              </div>
              <div class="text-xs text-dim">${window.UI.esc(r.regime || '')} · score ${window.UI.formatNumber(r.score, 2)}</div>
            </div>
          `).join('') || '<p class="text-dim">No MTF data.</p>'}
        </div>

        <div class="card">
          <h3 class="mb-4">Order Flow</h3>
          <div class="mono text-sm">
            <div class="flex justify-between mb-2"><span class="text-dim">delta regime</span><span>${window.UI.esc(orderFlow.delta_regime)}</span></div>
            <div class="flex justify-between mb-2"><span class="text-dim">cum delta</span><span>${window.UI.formatNumber(orderFlow.cumulative_delta)}</span></div>
            <div class="flex justify-between mb-2"><span class="text-dim">delta z</span><span>${window.UI.formatNumber(orderFlow.delta_z_score)}</span></div>
            <div class="flex justify-between mb-2"><span class="text-dim">POC</span><span>${window.UI.formatPrice(orderFlow.poc)}</span></div>
            <div class="flex justify-between mb-2"><span class="text-dim">VAH/VAL</span><span>${window.UI.formatPrice(orderFlow.vah)} / ${window.UI.formatPrice(orderFlow.val)}</span></div>
          </div>
        </div>

        <div class="card">
          <h3 class="mb-4">Agent Decision</h3>
          ${Object.entries(decision).map(([k, v]) => `
            <div class="mb-3">
              <div class="flex justify-between">
                <span class="text-sm text-dim">${window.UI.esc(k)}</span>
                <span class="badge ${v.verdict === 'ENTER' ? 'badge-teal' : v.verdict === 'REJECT' ? 'badge-red' : 'badge-amber'}">${window.UI.esc(v.verdict)}</span>
              </div>
              <div class="text-xs text-dim">confidence ${window.UI.formatPct((v.confidence || 0) * 100)} · ${(v.reasons || []).slice(0, 2).join('; ')}</div>
            </div>
          `).join('') || '<p class="text-dim">No decision data.</p>'}
        </div>
      </div>
    `;
  }

  function mount(container) {
    container.innerHTML = `
      <div class="page-header">
        <h1>Market <span class="page-subtitle">gold quant snapshot</span></h1>
      </div>
      <div class="skeleton" style="height:120px;border-radius:var(--radius-lg)"></div>
    `;

    window.UI.setLoading(true, 'Loading market data...');
    window.API.DataAPI.goldQuant().then((data) => {
      window.UI.setLoading(false);
      if (data && data.symbol) {
        renderQuant(container, data);
      } else {
        container.innerHTML = `
          <div class="page-header">
            <h1>Market <span class="page-subtitle">gold quant snapshot</span></h1>
          </div>
          <div class="empty-state">
            <div class="empty-title">Market data unavailable</div>
            <p class="empty-desc">The data service may be offline or gold quant engine not running.</p>
            <button class="btn btn-sm btn-primary mt-4" onclick="window.location.reload()">Retry</button>
          </div>
        `;
      }
    }).catch(() => {
      window.UI.setLoading(false);
      container.innerHTML = `
        <div class="page-header">
          <h1>Market <span class="page-subtitle">gold quant snapshot</span></h1>
        </div>
        <div class="empty-state">
          <div class="empty-title">Failed to load market data</div>
          <p class="empty-desc">Check data service connection and try again.</p>
          <button class="btn btn-sm btn-primary mt-4" onclick="window.location.reload()">Retry</button>
        </div>
      `;
    });
  }

  return { mount };
})();
