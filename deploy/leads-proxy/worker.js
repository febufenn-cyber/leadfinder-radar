/**
 * leadfinder.robofox.online — auth wall + proxy in front of the LeadFinder dashboard.
 * (leads.robofox.online was taken by an existing Next.js deployment.)
 *
 * The dashboard runs on the OCI VPS (localhost:8100) behind a Cloudflare quick
 * tunnel. On every tunnel (re)start, deploy/vps/leads_tunnel_vps.sh announces the
 * new *.trycloudflare.com origin to POST /__announce (shared-secret header) and
 * this Worker persists it to KV — no Cloudflare credentials live on the VPS.
 * The dashboard itself has no auth, so the Worker enforces Basic auth before
 * anything is proxied.
 */

function unauthorized() {
  return new Response("auth required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="leadfinder"' },
  });
}

async function handleAnnounce(request, env) {
  if (request.method !== "POST") return new Response(null, { status: 405 });
  if (
    !env.ANNOUNCE_TOKEN ||
    request.headers.get("x-announce-token") !== env.ANNOUNCE_TOKEN
  ) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }
  const body = await request.json().catch(() => null);
  const origin = body?.origin ?? "";
  if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(origin)) {
    return Response.json({ error: "bad_origin" }, { status: 400 });
  }
  await env.CONFIG.put("origin", origin);
  return Response.json({ ok: true, origin });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/__announce") return handleAnnounce(request, env);

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
      return new Response("tunnel offline — the VPS hasn't published an origin yet", {
        status: 503,
      });
    }

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
