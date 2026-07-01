/**
 * Site configuration — deployed URLs
 *
 * Uses stable custom domains for function endpoints.
 * These domains do not change across function redeployments.
 */
window.APP_CONFIG = {
  // Appwrite project endpoint
  endpoint: 'https://sgp.cloud.appwrite.io/v1',
  projectId: '6a22a362002b9ae880bb',

  // Function endpoints (stable custom domains)
  authFunctionUrl: 'https://auth.mrme.tech',
  pinFunctionUrl: 'https://pin.mrme.tech',
  internalFunctionUrl: 'https://internal.mrme.tech',

  // Site URL (native Appwrite custom domain)
  siteUrl: 'https://app.mrme.tech'
};
