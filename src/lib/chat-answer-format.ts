export type ChatAnswerBlock =
  | { type: "text"; text: string }
  | { type: "table"; headers: string[]; rows: string[][] };

function cells(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
  return trimmed.slice(1, -1).split("|").map((cell) =>
    cell.replace(/\s*<br\s*\/?>\s*/gi, ", ").trim());
}

export function parseChatAnswer(answer: string): ChatAnswerBlock[] {
  const lines = answer.split(/\r?\n/);
  const blocks: ChatAnswerBlock[] = [];
  const textLines: string[] = [];
  const flushText = () => {
    const text = textLines.join("\n").trim();
    if (text) blocks.push({ type: "text", text });
    textLines.length = 0;
  };

  for (let index = 0; index < lines.length;) {
    const headers = cells(lines[index]);
    const divider = cells(lines[index + 1] || "");
    if (headers && headers.length >= 2 && divider?.length === headers.length &&
        divider.every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const rows: string[][] = [];
      let next = index + 2;
      while (next < lines.length) {
        const row = cells(lines[next]);
        if (!row || row.length !== headers.length) break;
        rows.push(row);
        next += 1;
      }
      if (rows.length) {
        flushText();
        blocks.push({ type: "table", headers, rows });
        index = next;
        continue;
      }
    }
    textLines.push(lines[index]);
    index += 1;
  }
  flushText();
  return blocks;
}
