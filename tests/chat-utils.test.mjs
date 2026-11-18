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

test("keeps the bot's recommended product attached to its own chat history", () => {
  assert.deepEqual(chatUtils.buildSessionChatRequest?.("session-1", "แล้ว Strava ล่ะ", [
    { role: "assistant", content: "ขอแนะนำรุ่นนี้ครับ", product_name: "Black Shark GS3" },
  ]), {
    session_id: "session-1",
    question: "แล้ว Strava ล่ะ",
    history: [{ role: "assistant", content: "ขอแนะนำรุ่นนี้ครับ", product_name: "Black Shark GS3" }],
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

test("truncates overlong history turns so the session stays valid", () => {
  const longText = "ยาว".repeat(2000);
  const request = chatUtils.buildSessionChatRequest?.("session-1", "คำถาม", [
    { role: "assistant", content: longText },
  ]);
  assert.ok(request.history[0].content.length <= 900);
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
