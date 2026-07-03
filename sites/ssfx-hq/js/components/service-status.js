/**
 * SSFX HQ — Service Status Monitor
 * 
 * Shows health status of upstream and dependent services.
 * Can be shown to unauthenticated users on the landing page.
 */
window.ServiceStatusComponent = (function () {
  const SERVICE_CHECKS = [
    {
      id: 'auth',
      name: 'Auth Service',
      endpoint: () => `${window.API.CFG.authDomain}/health`,
      method: 'GET',
      expectedStatus: 200,
    },
    {
      id: 'v2',
      name: 'SSFX v2 API',
      endpoint: () => `${window.API.CFG.v2ApiBase}/health`,
      method: 'GET',
      expectedStatus: 200,
    },
    {
      id: 'dataservice',
      name: 'Data Service',
      endpoint: () => `${window.API.CFG.dataserviceBase}/api/v1/health`,
      method: 'GET',
      expectedStatus: 200,
    },
    {
      id: 'agent',
      name: 'Agent Harness',
      endpoint: () => `${window.API.CFG.agentHarnessBase}/health`,
      method: 'GET',
      expectedStatus: 200,
    },
    {
      id: 'cpr00-in',
      name: 'cpr00-in (AlwaysData)',
      endpoint: () => 'https://cpr00-in.alwaysdata.net/health',
      method: 'GET',
      expectedStatus: 200,
    },
  ];

  const statusCache = {};
  let isChecking = false;

  function getStatusClass(status) {
    if (status === 'healthy') return 'badge-green';
    if (status === 'unhealthy') return 'badge-red';
    if (status === 'unknown') return 'badge-gray';
    if (status === 'checking') return 'badge-blue';
    return 'badge-gray';
  }

  async function checkService(service) {
    try {
      const url = service.endpoint();
      const response = await fetch(url, {
        method: service.method,
        credentials: 'omit', // No credentials for public health checks
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (response.status === service.expectedStatus) {
        return 'healthy';
      } else {
        return 'unhealthy';
      }
    } catch (error) {
      return 'unhealthy';
    }
  }

  async function checkAllServices() {
    if (isChecking) return;
    isChecking = true;

    // Set all to checking state
    SERVICE_CHECKS.forEach(service => {
      statusCache[service.id] = 'checking';
    });

    try {
      const results = await Promise.all(
        SERVICE_CHECKS.map(async (service) => {
          const status = await checkService(service);
          return { id: service.id, status };
        })
      );

      results.forEach(result => {
        statusCache[result.id] = result.status;
      });
    } catch (error) {
      console.error('Service status check failed:', error);
    } finally {
      isChecking = false;
    }
  }

  function renderServiceStatus(service) {
    const status = statusCache[service.id] || 'unknown';
    return `
      <div class="service-status-item">
        <span class="service-name">${window.UI.esc(service.name)}</span>
        <span class="badge ${getStatusClass(status)}">${status}</span>
      </div>
    `;
  }

  function renderStatusIndicator() {
    const allHealthy = SERVICE_CHECKS.every(service => 
      statusCache[service.id] === 'healthy'
    );
    const someUnhealthy = SERVICE_CHECKS.some(service => 
      statusCache[service.id] === 'unhealthy'
    );
    const checking = SERVICE_CHECKS.some(service => 
      statusCache[service.id] === 'checking'
    );

    let statusText = 'Unknown';
    let statusClass = 'badge-gray';

    if (checking) {
      statusText = 'Checking...';
      statusClass = 'badge-blue';
    } else if (someUnhealthy) {
      statusText = 'Degraded';
      statusClass = 'badge-red';
    } else if (allHealthy) {
      statusText = 'All Systems Operational';
      statusClass = 'badge-green';
    }

    return `
      <div class="service-status-summary">
        <span class="badge ${statusClass}">${statusText}</span>
        <button id="refresh-status-btn" class="btn btn-sm btn-ghost">Refresh</button>
      </div>
    `;
  }

  function mount(container) {
    let intervalId = null;

    async function render() {
      await checkAllServices();

      const servicesHtml = SERVICE_CHECKS.map(renderServiceStatus).join('');
      const summaryHtml = renderStatusIndicator();

      container.innerHTML = `
        <div class="service-status-card">
          <h3>Service Status</h3>
          ${summaryHtml}
          <div class="service-status-grid">
            ${servicesHtml}
          </div>
        </div>
      `;

      // Set up refresh button
      const refreshBtn = container.querySelector('#refresh-status-btn');
      if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
          await checkAllServices();
          render();
        });
      }
    }

    // Initial render
    render();

    // Set up auto-refresh every 60 seconds
    intervalId = setInterval(() => {
      if (!isChecking) {
        checkAllServices();
        render();
      }
    }, 60000);

    // Clean up on unmount
    return () => {
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }

  return { mount };
})();