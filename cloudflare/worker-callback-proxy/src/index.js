/**
 * Cloudflare Worker — Stable Callback Proxy (Appwrite API Execution Mode)
 *
 * Provides a constant domain (auth.mrme.tech) for Appwrite Function endpoints.
 * Instead of relying on auto-generated function URLs, this worker calls
 * the Appwrite REST API to execute functions with proper authentication.
 *
 * Routes:
 *   /callback                 -> ctrader-auth function (OAuth callback)
 *   /auth/ctrader/start       -> ctrader-auth function (OAuth start)
 *   /session                  -> ctrader-auth function (session check)
 *   /logout                   -> ctrader-auth function (logout)
 *   /pin-login                -> ctrader-pin-auth function
 *   /set-credentials          -> ctrader-pin-auth function
 *   /pin-reset/*              -> ctrader-pin-auth function
 *   /internal/*               -> ctrader-internal function (requires x-internal-key)
 *
 * Environment variables:
 *   APPWRITE_ENDPOINT         - Appwrite API endpoint (e.g. https://sgp.cloud.appwrite.io/v1)
 *   APPWRITE_PROJECT_ID       - Appwrite project ID
 *   APPWRITE_API_KEY          - Appwrite server API key
 *   CTRADER_AUTH_FUNCTION_ID  - Function ID for ctrader-auth
 *   CTRADER_PIN_FUNCTION_ID   - Function ID for ctrader-pin-auth
 *   CTRADER_INTERNAL_FUNCTION_ID - Function ID for ctrader-internal
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Determine target function based on path
    let functionId = null;

    if (
      path === "/callback" ||
      path === "/auth/ctrader/start" ||
      path === "/session" ||
      path === "/logout"
    ) {
      functionId = env.CTRADER_AUTH_FUNCTION_ID;
    } else if (
      path === "/pin-login" ||
      path === "/set-credentials" ||
      path.startsWith("/pin-reset/")
    ) {
      functionId = env.CTRADER_PIN_FUNCTION_ID;
    } else if (path.startsWith("/internal/")) {
      functionId = env.CTRADER_INTERNAL_FUNCTION_ID;
    }

    if (!functionId) {
      return new Response(
        JSON.stringify({
          error: "Not found",
          path: path,
          message: "No function mapping for this path",
        }),
        {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    try {
      // Build the execution request payload
      const executionBody = {
        body: ["GET", "HEAD"].includes(request.method)
          ? ""
          : await request.text(),
        async: false,
        path: path + url.search,
        method: request.method,
        headers: {},
      };

      // Forward specific headers to the function
      const forwardHeaders = [
        "cookie",
        "x-internal-key",
        "content-type",
        "x-forwarded-for",
        "user-agent",
      ];
      for (const h of forwardHeaders) {
        const val = request.headers.get(h);
        if (val) {
          executionBody.headers[h] = val;
        }
      }

      // Call Appwrite Function Execution API
      const apiUrl = `${env.APPWRITE_ENDPOINT}/functions/${functionId}/executions`;
      const apiResponse = await fetch(apiUrl, {
        method: "POST",
        headers: {
          "X-Appwrite-Project": env.APPWRITE_PROJECT_ID,
          "X-Appwrite-Key": env.APPWRITE_API_KEY,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(executionBody),
      });

      const apiData = await apiResponse.json();

      if (apiData.status === "failed") {
        return new Response(
          JSON.stringify({
            error: "Function execution failed",
            detail: apiData.errors || "Unknown error",
            executionId: apiData.$id,
          }),
          {
            status: 502,
            headers: { "Content-Type": "application/json" },
          }
        );
      }

      // Build response from function output
      const statusCode = apiData.responseStatusCode || 200;
      const responseHeaders = new Headers();

      // Add CORS headers
      responseHeaders.set("Access-Control-Allow-Origin", "*");
      responseHeaders.set(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS"
      );
      responseHeaders.set(
        "Access-Control-Allow-Headers",
        "Content-Type, Authorization, x-internal-key"
      );

      // Forward function response headers
      if (apiData.responseHeaders && Array.isArray(apiData.responseHeaders)) {
        for (const h of apiData.responseHeaders) {
          // Skip headers that conflict with our CORS or are internal
          const name = h.name.toLowerCase();
          if (
            name === "content-length" ||
            name === "date" ||
            name === "connection" ||
            name === "keep-alive" ||
            name.startsWith("x-appwrite")
          ) {
            continue;
          }
          responseHeaders.set(h.name, h.value);
        }
      }

      // Set content-type if not already set by function
      if (!responseHeaders.has("content-type")) {
        if (
          apiData.responseBody &&
          (apiData.responseBody.startsWith("{") ||
            apiData.responseBody.startsWith("["))
        ) {
          responseHeaders.set("Content-Type", "application/json");
        } else {
          responseHeaders.set("Content-Type", "text/plain");
        }
      }

      return new Response(apiData.responseBody || "", {
        status: statusCode,
        headers: responseHeaders,
      });
    } catch (err) {
      return new Response(
        JSON.stringify({
          error: "Proxy error",
          detail: err.message,
          target: functionId,
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
  },
};
