import test from "node:test";
import assert from "node:assert/strict";

import { GET, POST } from "../src/app/api/[...path]/route.ts";

test("catalog request goes to the configured Python API", async () => {
  const originalFetch = globalThis.fetch;
  const originalUrl = process.env.PYTHON_API_URL;
  process.env.PYTHON_API_URL = "https://python.example";
  globalThis.fetch = async (url) => {
    assert.equal(url, "https://python.example/api/catalog");
    return Response.json({ products: [] });
  };
  try {
    const response = await GET(new Request("https://web.example/api/catalog"), { params: Promise.resolve({ path: ["catalog"] }) });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { products: [] });
  } finally {
    globalThis.fetch = originalFetch;
    if (originalUrl === undefined) delete process.env.PYTHON_API_URL;
    else process.env.PYTHON_API_URL = originalUrl;
  }
});

test("chat request forwards its JSON to Python", async () => {
  const originalFetch = globalThis.fetch;
  const originalUrl = process.env.PYTHON_API_URL;
  process.env.PYTHON_API_URL = "https://python.example";
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "https://python.example/api/chat");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { question: "สเปก", product_name: "Black Shark T11" });
    return Response.json({ status: "answered", answer: "จากไฟล์สินค้า" });
  };
  try {
    const request = new Request("https://web.example/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: "สเปก", product_name: "Black Shark T11" }),
    });
    const response = await POST(request, { params: Promise.resolve({ path: ["chat"] }) });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).answer, "จากไฟล์สินค้า");
  } finally {
    globalThis.fetch = originalFetch;
    if (originalUrl === undefined) delete process.env.PYTHON_API_URL;
    else process.env.PYTHON_API_URL = originalUrl;
  }
});

test("unknown API paths are not proxied", async () => {
  const response = await GET(new Request("https://web.example/api/private"), { params: Promise.resolve({ path: ["private"] }) });
  assert.equal(response.status, 404);
});
