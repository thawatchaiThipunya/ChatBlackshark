type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: RouteContext) {
  const { path } = await context.params;
  const endpoint = path.join("/");
  const getRoutes = [
    "catalog", "health", "chat/sessions", "auth/me", "auth/sso/login", "auth/sso/callback",
    "admin/dashboard", "admin/preferences", "admin/logs", "llm/keys", "llm/config", "llm/models", "llm/persona",
  ];
  if (!((request.method === "GET" && (getRoutes.includes(endpoint) || /^chat\/sessions\/[0-9a-f-]+\/messages$/.test(endpoint) || /^chat\/sessions\/[0-9a-f-]+\/media\/[0-9a-f-]+$/.test(endpoint))) ||
    (request.method === "POST" && (["chat", "chat/sessions", "chat/media/upload-url", "auth/logout", "admin/preferences", "llm/keys", "llm/config", "llm/persona", "llm/openrouter-key"].includes(endpoint))) ||
    (request.method === "DELETE" && /^llm\/keys\/\d+$/.test(endpoint)))) {
    return Response.json({ error: "not_found" }, { status: 404 });
  }

  const backend = process.env.PYTHON_API_URL || (process.env.VERCEL ? "" : "http://127.0.0.1:8000");
  if (!backend) return Response.json({ error: "python_api_not_configured" }, { status: 503 });

  try {
    const incoming = new URL(request.url);
    const response = await fetch(`${backend.replace(/\/$/, "")}/api/${endpoint}${incoming.search}`, {
      method: request.method,
      headers: {
        ...(request.method === "POST" ? { "Content-Type": "application/json" } : {}),
        ...(request.headers.get("cookie") ? { Cookie: request.headers.get("cookie")! } : {}),
        ...(request.headers.get("x-forwarded-host") || request.headers.get("host") ? {
          "X-Forwarded-Host": request.headers.get("x-forwarded-host") || request.headers.get("host")!,
        } : {}),
        ...(request.headers.get("x-forwarded-proto") ? { "X-Forwarded-Proto": request.headers.get("x-forwarded-proto")! } : {}),
        ...(request.headers.get("x-request-id") ? { "X-Request-ID": request.headers.get("x-request-id")! } : {}),
      },
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
    const headers = new Headers({
      "Content-Type": response.headers.get("Content-Type") || "application/json",
    });
    for (const name of ["Location", "Cache-Control", "Referrer-Policy", "Pragma", "X-Request-ID"]) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    const setCookies = (response.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie?.();
    if (setCookies?.length) {
      for (const cookie of setCookies) headers.append("Set-Cookie", cookie);
    } else if (response.headers.get("set-cookie")) {
      headers.set("Set-Cookie", response.headers.get("set-cookie")!);
    }
    return new Response(response.body, {
      status: response.status,
      headers,
    });
  } catch {
    return Response.json({ error: "python_api_unavailable" }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
