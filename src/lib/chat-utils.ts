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
  status: "answered" | "need_model" | "handoff_mock" | "key_required" | string;
  answer: string;
  reason?: string;
  product_name?: string;
  candidate_models?: string[];
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

export function buildUnifiedChatRequest(selectedName: string, question: string) {
  const cleanQuestion = question.trim();
  const cleanName = selectedName.trim();
  if (!cleanQuestion) throw new Error("กรุณาพิมพ์คำถามก่อนส่ง");
  if (!cleanName) throw new Error("กรุณาเลือกรุ่นสินค้าก่อนส่งคำถาม");
  return { question: cleanQuestion, model_name: cleanName };
}

export type ChatHistoryTurn = {
  role: "user" | "assistant";
  content: string;
  product_name?: string;
  product_names?: string[];
  card_title?: string;
  card_product_names?: string[];
};

export function mergeLoadedSessions<T extends { id: string; updatedAt?: number }>(loaded: T[], current: T[]): T[] {
  const currentById = new Map(current.map((session) => [session.id, session]));
  const loadedIds = new Set(loaded.map((session) => session.id));
  return [
    ...loaded.map((session) => currentById.get(session.id) ?? session),
    ...current.filter((session) => !loadedIds.has(session.id)),
  ].sort((left, right) => (right.updatedAt ?? 0) - (left.updatedAt ?? 0));
}

export function shouldApplyInitialSession(preferredId: string | null, startVersion: number,
                                          currentVersion: number): boolean {
  return Boolean(preferredId) && startVersion === currentVersion;
}

export function buildSessionChatRequest(sessionId: string, question: string, history: ChatHistoryTurn[]) {
  const cleanQuestion = question.trim();
  if (!cleanQuestion) throw new Error("กรุณาพิมพ์คำถามก่อนส่ง");
  if (!sessionId.trim()) throw new Error("ไม่พบ session ของแชท กรุณาเริ่มแชทใหม่");
  const trimmedHistory = history.slice(-12).map((turn) => ({
    ...turn,
    content: turn.content.length > 900 ? turn.content.slice(0, 900) : turn.content,
  }));
  return { session_id: sessionId, question: cleanQuestion, history: trimmedHistory };
}

export function presentChatReply(reply: ChatReply) {
  const isHandoff = reply.status === "handoff_mock" || reply.status === "escalated" ||
    reply.handoff_mock === true || reply.handoff_sent === true;
  const isMockHandoff = reply.status === "handoff_mock" || reply.handoff_mock === true;
  const needsKey = reply.status === "key_required" || reply.reason === "llm_key_missing";
  const temporaryFailure = reply.status === "temporarily_unavailable";

  return {
    kind: needsKey ? "setup_required" : isHandoff ? "handoff" : temporaryFailure ? "error" : reply.status === "need_model" ? "need_model" : "answer",
    answer: typeof reply.answer === "string" ? reply.answer : "",
    sources: isMockHandoff ? [] : Array.isArray(reply.sources) ? reply.sources : [],
    handoffSent: reply.handoff_sent === true,
    ...(typeof reply.product_name === "string" ? { productName: reply.product_name } : {}),
    ...(typeof reply.case_id === "number" ? { caseId: reply.case_id } : {}),
  } as const;
}
