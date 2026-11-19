"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Bot, Check, Eye, EyeOff, KeyRound, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { ConsolePage } from "@/components/ConsolePage";

type Provider = "gemini" | "openrouter";
type KeyRow = { id: number; label: string; last4: string; created_at: string };
type LlmConfig = {
  provider: Provider;
  gemini_intent_model: string;
  gemini_vision_model: string;
  gemini_answer_model: string;
  openrouter_intent_model: string;
  openrouter_vision_model: string;
  openrouter_answer_model: string;
  openrouter_key_last4: string | null;
};
type LlmConfigPayload = Omit<LlmConfig, "openrouter_key_last4">;

const modelFields = [
  { key: "intent", title: "Intent", description: "จับรุ่นสินค้าและหัวข้อ FAQ" },
  { key: "vision", title: "Vision", description: "อ่านภาพและวิดีโอที่แนบ" },
  { key: "answer", title: "Answer", description: "สร้างคำตอบที่ส่งให้ลูกค้า" },
] as const;

async function requestJson<T>(path: string, method = "GET", body?: object): Promise<T> {
  const response = await fetch(path, {
    method,
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 401) {
    window.location.assign("/login?error=signed_out");
    throw new Error("เซสชันหมดอายุ กำลังกลับไปหน้าเข้าสู่ระบบ");
  }
  if (!response.ok) throw new Error("เชื่อมต่อบริการจัดการ LLM ไม่สำเร็จ");
  return await response.json() as T;
}

function configPayload(config: LlmConfig): LlmConfigPayload {
  const { openrouter_key_last4: _maskedKey, ...payload } = config;
  return payload;
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("th-TH", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

export default function LlmKeysPage() {
  const [config, setConfig] = useState<LlmConfig | null>(null);
  const [savedConfig, setSavedConfig] = useState<LlmConfig | null>(null);
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [keysLoaded, setKeysLoaded] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [geminiLabel, setGeminiLabel] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [openRouterKey, setOpenRouterKey] = useState("");
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenRouterKey, setShowOpenRouterKey] = useState(false);

  const refresh = useCallback(async () => {
    setBusyAction("refresh");
    setError("");
    setNotice("");
    const [keyResult, configResult] = await Promise.allSettled([
      requestJson<{ keys: KeyRow[] }>("/api/llm/keys"),
      requestJson<LlmConfig>("/api/llm/config"),
    ]);
    const failures: string[] = [];
    if (keyResult.status === "fulfilled") {
      setKeys(Array.isArray(keyResult.value.keys) ? keyResult.value.keys : []);
      setKeysLoaded(true);
    } else failures.push("รายการ Gemini keys");
    if (configResult.status === "fulfilled") {
      setConfig(configResult.value);
      setSavedConfig(configResult.value);
      setConfigLoaded(true);
    } else failures.push("การตั้งค่า provider และโมเดล");
    if (failures.length) setError(`โหลด${failures.join(" และ")}ไม่สำเร็จ กดรีเฟรชเพื่อลองอีกครั้ง`);
    setBusyAction("");
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const providerChanged = !!(config && savedConfig && JSON.stringify(configPayload(config)) !== JSON.stringify(configPayload(savedConfig)));
  const hasModels = !!config && [
    config.gemini_intent_model, config.gemini_vision_model, config.gemini_answer_model,
    config.openrouter_intent_model, config.openrouter_vision_model, config.openrouter_answer_model,
  ].every((model) => model.trim().length >= 2);
  const canSaveConfig = !!config && providerChanged && hasModels && !(config.provider === "openrouter" && !config.openrouter_key_last4) && !busyAction;

  function updateModel(provider: Provider, field: typeof modelFields[number]["key"], value: string) {
    if (!config) return;
    const key = `${provider}_${field}_model` as keyof LlmConfig;
    setConfig({ ...config, [key]: value });
  }

  async function saveConfig(event: FormEvent) {
    event.preventDefault();
    if (!config) return;
    setBusyAction("config"); setError(""); setNotice("");
    try {
      const saved = await requestJson<LlmConfig>("/api/llm/config", "POST", configPayload(config));
      setConfig(saved); setSavedConfig(saved); setNotice("บันทึก provider และโมเดลแล้ว");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "บันทึกการตั้งค่าไม่สำเร็จ");
    } finally { setBusyAction(""); }
  }

  async function saveOpenRouterKey(event: FormEvent) {
    event.preventDefault();
    setBusyAction("openrouter-key"); setError(""); setNotice("");
    try {
      const saved = await requestJson<LlmConfig>("/api/llm/openrouter-key", "POST", { key: openRouterKey });
      setConfig((current) => current ? { ...current, openrouter_key_last4: saved.openrouter_key_last4 } : saved);
      setSavedConfig((current) => current ? { ...current, openrouter_key_last4: saved.openrouter_key_last4 } : saved);
      setOpenRouterKey(""); setShowOpenRouterKey(false); setNotice("บันทึก OpenRouter key แล้ว ระบบจะแสดงเฉพาะ 4 ตัวท้าย");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "บันทึก OpenRouter key ไม่สำเร็จ");
    } finally { setBusyAction(""); }
  }

  async function addGeminiKey(event: FormEvent) {
    event.preventDefault();
    setBusyAction("gemini-key"); setError(""); setNotice("");
    try {
      await requestJson<{ id: number }>("/api/llm/keys", "POST", { label: geminiLabel, key: geminiKey });
      const result = await requestJson<{ keys: KeyRow[] }>("/api/llm/keys");
      setKeys(Array.isArray(result.keys) ? result.keys : []); setKeysLoaded(true);
      setGeminiLabel(""); setGeminiKey(""); setShowGeminiKey(false); setNotice("เพิ่ม Gemini key แล้ว ระบบเก็บค่าแบบเข้ารหัส");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "เพิ่ม Gemini key ไม่สำเร็จ");
    } finally { setBusyAction(""); }
  }

  async function removeGeminiKey(row: KeyRow) {
    if (!window.confirm(`ลบคีย์ “${row.label}” ที่ลงท้าย ${row.last4} ใช่ไหม`)) return;
    setBusyAction(`delete-${row.id}`); setError(""); setNotice("");
    try {
      await requestJson<{ id: number }>(`/api/llm/keys/${row.id}`, "DELETE");
      setKeys((current) => current.filter((key) => key.id !== row.id));
      setNotice(`ลบ “${row.label}” แล้ว`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "ลบ Gemini key ไม่สำเร็จ");
    } finally { setBusyAction(""); }
  }

  return <ConsolePage icon={Bot} title="โมเดลและ API keys" subtitle="เลือก provider ตั้งค่าโมเดล และจัดการคีย์สำหรับคำตอบสินค้า"
    actions={<button className="header-action" type="button" onClick={() => void refresh()} disabled={!!busyAction}><RefreshCw size={15} /> รีเฟรช</button>}>
    {error && <p className="load-error" role="alert">{error}</p>}
    {notice && <p className="setting-feedback" role="status">{notice}</p>}

    <section className="llm-status-strip" aria-label="สถานะการตั้งค่า LLM">
      <div className="llm-status-cell"><span>Provider ที่กำลังใช้</span><strong>{configLoaded ? config?.provider === "openrouter" ? "OpenRouter" : "Gemini" : "กำลังโหลด"}</strong></div>
      <div className="llm-status-cell"><span>Gemini keys</span><strong>{keysLoaded ? `${keys.length} คีย์` : "กำลังโหลด"}</strong></div>
      <div className="llm-status-cell"><span>OpenRouter key</span><strong>{configLoaded ? config?.openrouter_key_last4 ? `•••• ${config.openrouter_key_last4}` : "ยังไม่ได้เพิ่ม" : "กำลังโหลด"}</strong></div>
    </section>

    <section className="llm-panel llm-routing-panel" aria-labelledby="llm-routing-title">
      <div className="panel-heading"><div><h2 id="llm-routing-title">Provider และโมเดล</h2><p>ตั้งค่าโมเดลแยกตามงานของบอท แล้วเลือก provider ที่ใช้สร้างคำตอบ</p></div><Bot size={19} /></div>
      {!configLoaded || !config ? <p className="empty-copy">กำลังโหลดการตั้งค่า provider…</p> : <form onSubmit={(event) => void saveConfig(event)}>
        <fieldset className="provider-options">
          <legend>Provider ที่ใช้งาน</legend>
          <label className="provider-option"><input type="radio" name="provider" value="gemini" checked={config.provider === "gemini"} onChange={() => setConfig({ ...config, provider: "gemini" })} />
            <span><strong>Gemini</strong><small>หมุนเวียนคีย์ที่บันทึกไว้ และลองคีย์ถัดไปเมื่อเกิดข้อผิดพลาด</small></span></label>
          <label className="provider-option"><input type="radio" name="provider" value="openrouter" checked={config.provider === "openrouter"} onChange={() => setConfig({ ...config, provider: "openrouter" })} />
            <span><strong>OpenRouter</strong><small>เลือกโมเดลแยกตามขั้นตอน และใช้ API key เดียว</small></span></label>
        </fieldset>

        <div className="llm-model-grid">
          {(["gemini", "openrouter"] as const).map((provider) => <fieldset className="llm-model-group" data-active={config.provider === provider} key={provider}>
            <legend>{provider === "gemini" ? "โมเดล Gemini" : "โมเดล OpenRouter"}{config.provider === provider && <span>กำลังใช้งาน</span>}</legend>
            {modelFields.map((field) => {
              const id = `${provider}-${field.key}-model`;
              const configKey = `${provider}_${field.key}_model` as keyof LlmConfig;
              return <label htmlFor={id} key={field.key}>{field.title}<small>{field.description}</small>
                <input id={id} value={config[configKey] as string} maxLength={120} minLength={2} required
                  onChange={(event) => updateModel(provider, field.key, event.target.value)} />
              </label>;
            })}
          </fieldset>)}
        </div>

        {config.provider === "openrouter" && !config.openrouter_key_last4 && <p className="llm-note" role="status">เพิ่ม OpenRouter key ก่อนบันทึกการสลับ provider</p>}
        <div className="llm-actions">
          <span className="llm-note">โมเดลและ provider มีผลกับคำขอใหม่</span>
          <div className="choice-row">
            {providerChanged && <button className="setting-choice" type="button" disabled={!!busyAction} onClick={() => savedConfig && setConfig(savedConfig)}>ยกเลิกการแก้ไข</button>}
            <button className="llm-primary-action" type="submit" disabled={!canSaveConfig}><Check size={15} /> บันทึกการตั้งค่า</button>
          </div>
        </div>
      </form>}
    </section>

    <div className="llm-secret-grid">
      <section className="llm-panel" aria-labelledby="openrouter-key-title">
        <div className="panel-heading"><div><h2 id="openrouter-key-title">OpenRouter key</h2><p>เก็บแบบเข้ารหัส และแสดงเพียงท้าย 4 ตัวหลังบันทึก</p></div><KeyRound size={18} /></div>
        <div className="llm-openrouter-state"><ShieldCheck size={16} />{config?.openrouter_key_last4 ? `บันทึกแล้ว · •••• ${config.openrouter_key_last4}` : "ยังไม่มี OpenRouter key"}</div>
        <form className="llm-form" onSubmit={(event) => void saveOpenRouterKey(event)}>
          <div className="llm-key-input"><label htmlFor="openrouter-key">API key ใหม่</label>
            <div className="llm-password-wrap"><input id="openrouter-key" type={showOpenRouterKey ? "text" : "password"} value={openRouterKey} onChange={(event) => setOpenRouterKey(event.target.value)} minLength={10} maxLength={500} required autoComplete="new-password" placeholder="วาง OpenRouter API key" />
              <button type="button" onClick={() => setShowOpenRouterKey((shown) => !shown)} aria-label={showOpenRouterKey ? "ซ่อน OpenRouter key" : "แสดง OpenRouter key"}>{showOpenRouterKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
          </div>
          <button type="submit" disabled={!!busyAction || openRouterKey.trim().length < 10}><KeyRound size={15} /> บันทึก OpenRouter key</button>
        </form>
        <p className="llm-note">การบันทึก key ใหม่จะแทนค่าเดิม</p>
      </section>

      <section className="llm-panel" aria-labelledby="gemini-keys-title">
        <div className="panel-heading"><div><h2 id="gemini-keys-title">Gemini keys</h2><p>{keysLoaded ? `${keys.length} คีย์ที่บันทึกไว้` : "ตรวจสอบรายการคีย์"}</p></div><KeyRound size={18} /></div>
        {!keysLoaded ? <p className="empty-copy">กำลังโหลดรายการคีย์…</p> : keys.length ? <ul className="llm-key-list">{keys.map((row) => <li key={row.id}>
          <div><strong>{row.label}</strong><small>•••• {row.last4}</small></div>
          <time dateTime={row.created_at}>{formatDate(row.created_at)}</time>
          <button type="button" disabled={!!busyAction} onClick={() => void removeGeminiKey(row)} aria-label={`ลบ Gemini key ${row.label}`}><Trash2 size={14} /><span>ลบ</span></button>
        </li>)}</ul> : <div className="llm-empty-state"><strong>ยังไม่มี Gemini key</strong><p>เพิ่มคีย์แรกด้านล่างเพื่อให้ระบบหมุนเวียนใช้สร้างคำตอบ</p></div>}
        <form className="llm-form" onSubmit={(event) => void addGeminiKey(event)}>
          <div className="llm-key-input"><label htmlFor="gemini-key-label">ชื่อเรียกคีย์</label><input id="gemini-key-label" value={geminiLabel} onChange={(event) => setGeminiLabel(event.target.value)} maxLength={80} required placeholder="เช่น Gemini คีย์หลัก" /></div>
          <div className="llm-key-input"><label htmlFor="gemini-key">Gemini API key</label>
            <div className="llm-password-wrap"><input id="gemini-key" type={showGeminiKey ? "text" : "password"} value={geminiKey} onChange={(event) => setGeminiKey(event.target.value)} minLength={10} maxLength={300} required autoComplete="new-password" placeholder="วาง Gemini API key" />
              <button type="button" onClick={() => setShowGeminiKey((shown) => !shown)} aria-label={showGeminiKey ? "ซ่อน Gemini key" : "แสดง Gemini key"}>{showGeminiKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>
          </div>
          <button type="submit" disabled={!!busyAction || !geminiLabel.trim() || geminiKey.trim().length < 10}><Plus size={15} /> เพิ่ม Gemini key</button>
        </form>
        <p className="llm-note">ค่าคีย์เต็มจะไม่แสดงหลังบันทึก</p>
      </section>
    </div>
  </ConsolePage>;
}
