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

test("reports catalog connection state accurately", () => {
  assert.equal(chatUtils.getCatalogStatus?.(true, false), "loading");
  assert.equal(chatUtils.getCatalogStatus?.(false, true), "offline");
  assert.equal(chatUtils.getCatalogStatus?.(false, false), "ready");
});
