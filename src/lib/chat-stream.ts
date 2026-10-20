/** Decode NDJSON independently of TCP/HTTP chunk boundaries. */
export function createChatStreamParser(onText: (text: string) => void) {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let pending = "";

  function record(line: string) {
    if (!line.trim()) return; // Legacy Streamer can add blank lines.
    const value: unknown = JSON.parse(line);
    if (typeof value !== "string") {
      throw new Error("Invalid chat stream record");
    }
    onText(value);
  }

  function consume() {
    let newline: number;
    while ((newline = pending.indexOf("\n")) !== -1) {
      record(pending.slice(0, newline));
      pending = pending.slice(newline + 1);
    }
  }

  return {
    push(bytes: Uint8Array) {
      pending += decoder.decode(bytes, { stream: true });
      consume();
    },
    finish() {
      pending += decoder.decode();
      consume();
      if (pending.trim()) record(pending);
      pending = "";
    },
  };
}
