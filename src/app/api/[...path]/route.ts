type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: RouteContext) {
  const { path } = await context.params;
  const endpoint = path.join("/");
  if (!((request.method === "GET" && ["catalog", "health"].includes(endpoint)) ||
    (request.method === "POST" && endpoint === "chat"))) {
    return Response.json({ error: "not_found" }, { status: 404 });
  }

  const backend = process.env.PYTHON_API_URL || (process.env.VERCEL ? "" : "http://127.0.0.1:8000");
  if (!backend) return Response.json({ error: "python_api_not_configured" }, { status: 503 });

  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/api/${endpoint}`, {
      method: request.method,
      headers: request.method === "POST" ? { "Content-Type": "application/json" } : undefined,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
    });
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return Response.json({ error: "python_api_unavailable" }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
