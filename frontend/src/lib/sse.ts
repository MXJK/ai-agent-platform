export type SseEvent = { event: string; data: unknown };

export function parseSseBlock(block: string): SseEvent | null {
  let event = 'message';
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
  }
  if (!data.length) return null;
  const raw = data.join('\n');
  try { return { event, data: JSON.parse(raw) }; }
  catch { return { event, data: { text: raw } }; }
}

/** Handles UTF-8, LF/CRLF and delimiters split across network chunks. */
export async function consumeSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const emit = (block: string) => {
    signal?.throwIfAborted();
    const parsed = parseSseBlock(block);
    if (parsed) onEvent(parsed.event, parsed.data);
  };
  const cancel = () => { void reader.cancel(signal?.reason).catch(() => undefined); };
  signal?.addEventListener('abort', cancel, { once: true });
  try {
    signal?.throwIfAborted();
    while (true) {
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        emit(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
      if (done) break;
    }
    if (buffer.trim()) emit(buffer);
  } finally {
    signal?.removeEventListener('abort', cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
