import { describe, expect, test } from "bun:test";
import { createChatStreamParser } from "../src/lib/chat-stream";

const encoder = new TextEncoder();
function read(chunks: Uint8Array[]) {
  let output = "";
  const parser = createChatStreamParser(text => { output += text; });
  for (const chunk of chunks) parser.push(chunk);
  parser.finish();
  return output;
}

describe("NDJSON chat transport", () => {
  test("multiple records in one HTTP chunk", () => {
    expect(read([encoder.encode('"Hello"\n" world"\n')])).toBe("Hello world");
  });
  test("records and UTF-8 code points split at every byte", () => {
    const wire = encoder.encode(JSON.stringify("città 🙂") + "\n" + JSON.stringify("\nnext") + "\n");
    expect(read(Array.from(wire, byte => new Uint8Array([byte])))).toBe("città 🙂\nnext");
  });
  test("all possible two-chunk splits preserve content", () => {
    const wire = encoder.encode('"Hello"\n" world"\n');
    for (let index = 0; index <= wire.length; index++) {
      expect(read([wire.slice(0, index), wire.slice(index)])).toBe("Hello world");
    }
  });
  test("legacy blank lines and final record without newline", () => {
    expect(read([encoder.encode('\n"Hello"\n\n" world"')])).toBe("Hello world");
  });
  test("malformed or truncated records fail visibly", () => {
    for (const raw of ['"Hello"" world"\n', '"incomplete', '{"output":"wrong protocol"}\n']) {
      expect(() => read([encoder.encode(raw)])).toThrow();
    }
  });
  test("invalid UTF-8 fails visibly", () => {
    expect(() => read([new Uint8Array([34, 0xff, 34, 10])])).toThrow();
  });
});
