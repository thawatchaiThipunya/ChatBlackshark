import assert from "node:assert/strict";
import test from "node:test";

const chatUtils = await import("../src/lib/chat-utils.ts").catch(() => ({}));

test("builds a trimmed specification request with the selected product", () => {
  assert.deepEqual(chatUtils.buildChatRequest?.("spec", "Black Shark T11", "  Game Mode?  "), {
    question: "Game Mode?",
    product_name: "Black Shark T11",
  });
});

test("builds a troubleshooting request with the selected model", () => {
  assert.deepEqual(chatUtils.buildChatRequest?.("troubleshooting", "Black Shark S1 Pro Smart Watch", "ชาร์จไม่เข้า"), {
    question: "ชาร์จไม่เข้า",
    model_name: "Black Shark S1 Pro Smart Watch",
  });
});

test("unified chat sends one model selection for specs and troubleshooting", () => {
  assert.deepEqual(chatUtils.buildUnifiedChatRequest?.("Black Shark T11", "  ชาร์จไม่เข้า  "), {
    question: "ชาร์จไม่เข้า", model_name: "Black Shark T11",
  });
});

test("sends the session id and lets the server load its history", () => {
  assert.deepEqual(chatUtils.buildSessionChatRequest?.("session-1", "แล้ว Strava ล่ะ", [
    { role: "assistant", content: "ขอแนะนำรุ่นนี้ครับ", product_name: "Black Shark GS3",
      product_names: ["Black Shark GS3", "Black Shark GS3 Ultra"] },
  ]), {
    session_id: "session-1",
    question: "แล้ว Strava ล่ะ",
  });
});

test("presents a mock handoff without implying a staff message was sent", () => {
  assert.deepEqual(
    chatUtils.presentChatReply?.({
      status: "handoff_mock",
      answer: "ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ",
      handoff_mock: true,
      handoff_sent: false,
      sources: [],
    }),
    {
      kind: "handoff",
      answer: "ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ",
      sources: [],
      handoffSent: false,
    },
  );
});

test("shows provider exhaustion as a temporary error, not a staff handoff", () => {
  const presented = chatUtils.presentChatReply?.({
    status: "temporarily_unavailable", reason: "llm_rate_limited",
    answer: "ลองอีกครั้ง", handoff_mock: false, handoff_sent: false,
  });
  assert.equal(presented?.kind, "error");
  assert.equal(presented?.handoffSent, false);
});

test("does not send long or overfull history back to the API", () => {
  const longText = "ยาว".repeat(2000);
  const request = chatUtils.buildSessionChatRequest?.("session-1", "คำถาม", [
    { role: "assistant", content: longText },
  ]);
  assert.deepEqual(request, { session_id: "session-1", question: "คำถาม" });
});

test("presents a real escalation as a sent handoff with case id", () => {
  const presented = chatUtils.presentChatReply?.({
    status: "escalated",
    answer: "ส่งเรื่องให้เจ้าหน้าที่แล้ว เคส #7",
    handoff_mock: false,
    handoff_sent: true,
    case_id: 7,
    sources: [],
  });
  assert.equal(presented.kind, "handoff");
  assert.equal(presented.handoffSent, true);
  assert.equal(presented.caseId, 7);
});

test("reports catalog connection state accurately", () => {
  assert.equal(chatUtils.getCatalogStatus?.(true, false), "loading");
  assert.equal(chatUtils.getCatalogStatus?.(false, true), "offline");
  assert.equal(chatUtils.getCatalogStatus?.(false, false), "ready");
});

test("preserves a locally created chat when the initial server list arrives late", () => {
  assert.deepEqual(chatUtils.mergeLoadedSessions?.(
    [{ id: "old", title: "เก่า", updatedAt: 1 }],
    [{ id: "new", title: "ใหม่", updatedAt: 2 }],
  ), [
    { id: "new", title: "ใหม่", updatedAt: 2 },
    { id: "old", title: "เก่า", updatedAt: 1 },
  ]);
});

test("does not restore a stale preferred chat after the user has navigated", () => {
  assert.equal(chatUtils.shouldApplyInitialSession?.("old", 0, 1), false);
  assert.equal(chatUtils.shouldApplyInitialSession?.("old", 1, 1), true);
});
