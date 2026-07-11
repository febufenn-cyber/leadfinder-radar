/**
 * leadfinder.robofox.online — auth wall + proxy in front of the LeadFinder dashboard.
 * (leads.robofox.online was taken by an existing Next.js deployment.)
 *
 * The dashboard runs on the Mac mini (localhost:8100) behind a Cloudflare quick
 * tunnel; the tunnel's current *.trycloudflare.com origin is published to KV by
 * scripts/leads_tunnel.sh every time the tunnel (re)starts. The dashboard itself
 * has no auth, so this Worker enforces Basic auth before anything is proxied.
 */

function unauthorized() {
  return new Response("auth required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="leadfinder"' },
  });
}

export default {
  async fetch(request, env) {
    const header = request.headers.get("Authorization") || "";
    if (!header.startsWith("Basic ")) return unauthorized();
    let user, pass;
    try {
      [user, pass] = atob(header.slice(6)).split(":");
    } catch {
      return unauthorized();
    }
    if (user !== env.BASIC_USER || pass !== env.BASIC_PASS) return unauthorized();

    const origin = await env.CONFIG.get("origin");
    if (!origin) {
      return new Response("tunnel offline — the Mac hasn't published an origin yet", {
        status: 503,
      });
    }

    const url = new URL(request.url);
    const target = new URL(origin);
    target.pathname = url.pathname;
    target.search = url.search;

    const headers = new Headers(request.headers);
    headers.delete("Authorization"); // the dashboard doesn't need the wall's creds

    const resp = await fetch(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
    });
    return new Response(resp.body, resp);
  },
};
