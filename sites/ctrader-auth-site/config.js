/**
 * Site configuration — deployed URLs
 * 
 * Uses stable custom domain (auth.mrme.tech) for function endpoints
 * instead of auto-generated Appwrite URLs that change per deployment.
 */
window.APP_CONFIG = {
  // Appwrite project endpoint (custom domain recommended for production)
  endpoint: 'https://sgp.cloud.appwrite.io/v1',
  projectId: '6a22a362002b9ae880bb',
  
  // Stable proxy domain for all function calls via Cloudflare Worker
  authFunctionUrl: 'https://auth.mrme.tech',
  pinFunctionUrl: 'https://auth.mrme.tech',
  
  // Site URL (Appwrite Site custom domain or auto-generated)
  siteUrl: 'https://6a44b8ee000475e8df39.appwrite.network'
};
