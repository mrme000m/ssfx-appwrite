/**
 * Cloudflare Worker — Stable Callback Proxy
 * 
 * Provides a constant domain (auth.mrme.tech) for Appwrite Function endpoints
 * that would otherwise change with each deployment.
 * 
 * Routes:
 *   /callback               -> ctrader-auth function (OAuth callback)
 *   /auth/ctrader/start     -> ctrader-auth function (OAuth start)
 *   /session                -> ctrader-auth function (session check)
 *   /logout                 -> ctrader-auth function (logout)
 *   /pin-login              -> ctrader-pin-auth function
 *   /set-credentials        -> ctrader-pin-auth function
 *   /pin-reset/*            -> ctrader-pin-auth function
 *   /internal/*             -> ctrader-internal function (requires x-internal-key)
 * 
 * Environment variables:
 *   CTRADER_AUTH_FUNCTION_URL       - Current ctrader-auth function URL
 *   CTRADER_PIN_FUNCTION_URL        - Current ctrader-pin-auth function URL
 *   CTRADER_INTERNAL_FUNCTION_URL   - Current ctrader-internal function URL
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    
    // Determine target function based on path
    let targetBaseUrl = null;
    
    if (path === '/callback' || 
        path === '/auth/ctrader/start' || 
        path === '/session' || 
        path === '/logout') {
      targetBaseUrl = env.CTRADER_AUTH_FUNCTION_URL;
    } else if (path === '/pin-login' || 
               path === '/set-credentials' || 
               path.startsWith('/pin-reset/')) {
      targetBaseUrl = env.CTRADER_PIN_FUNCTION_URL;
    } else if (path.startsWith('/internal/')) {
      targetBaseUrl = env.CTRADER_INTERNAL_FUNCTION_URL;
    }
    
    if (!targetBaseUrl) {
      return new Response(JSON.stringify({ 
        error: 'Not found',
        path: path,
        message: 'No function mapping for this path'
      }), { 
        status: 404,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    // Build target URL preserving path and query string
    const targetUrl = new URL(path + url.search, targetBaseUrl);
    
    // Clone request to preserve method, headers, and body
    const modifiedRequest = new Request(targetUrl, {
      method: request.method,
      headers: request.headers,
      body: request.body,
    });
    
    // Forward to Appwrite Function
    try {
      const response = await fetch(modifiedRequest);
      
      // Clone response to make headers mutable
      const modifiedResponse = new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
      
      // Add CORS headers if needed (Appwrite Functions may already handle this)
      modifiedResponse.headers.set('Access-Control-Allow-Origin', '*');
      modifiedResponse.headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      modifiedResponse.headers.set('Access-Control-Allow-Headers', 'Content-Type, Authorization, x-internal-key');
      
      return modifiedResponse;
    } catch (err) {
      return new Response(JSON.stringify({ 
        error: 'Proxy error',
        detail: err.message,
        target: targetUrl.toString()
      }), { 
        status: 502,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
};
