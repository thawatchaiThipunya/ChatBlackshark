export type ChatMode = "spec" | "troubleshooting";

export function getCatalogStatus(loading: boolean, failed: boolean): "loading" | "offline" | "ready" {
  if (loading) return "loading";
  return failed ? "offline" : "ready";
}

export interface ChatSource {
  file: string;
  sheet?: string;
  cell?: string;
}

export interface ChatReply {
  status: "answered" | "need_model" | "handoff_mock" | string;
  answer: string;
  handoff_mock?: boolean;
  handoff_sent?: boolean;
  sources?: ChatSource[];
  [key: string]: unknown;
}

export function buildChatRequest(mode: ChatMode, selectedName: string, question: string) {
  const cleanQuestion = question.trim();
  const cleanName = selectedName.trim();

  if (!cleanQuestion) throw new Error("กรุณาพิมพ์คำถามก่อนส่ง");
  if (!cleanName) throw new Error("กรุณาเลือกสินค้า หรือรุ่นก่อนส่งคำถาม");

  return mode === "spec"
    ? { question: cleanQuestion, product_name: cleanName }
    : { question: cleanQuestion, model_name: cleanName };
}

export function presentChatReply(reply: ChatReply) {
  const isMockHandoff = reply.status === "handoff_mock" || reply.handoff_mock === true;

  return {
    kind: isMockHandoff ? "handoff" : reply.status === "need_model" ? "need_model" : "answer",
    answer: typeof reply.answer === "string" ? reply.answer : "",
    sources: isMockHandoff ? [] : Array.isArray(reply.sources) ? reply.sources : [],
    handoffSent: reply.handoff_sent === true,
  } as const;
}
