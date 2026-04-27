/**
 * Generate a UUID v4 that works in all contexts.
 *
 * Uses `crypto.randomUUID()` as the primary method.
 * Falls back to `crypto.getRandomValues()` if `randomUUID` is unavailable
 * (e.g. when the bundler resolves `crypto` to a partial polyfill).
 */
export function generateUUID(): string {
  const c = (globalThis as any).crypto ?? (window as any).crypto;
  if (typeof c?.randomUUID === "function") {
    return c.randomUUID();
  }

  // Fallback for when `crypto.randomUUID` is missing
  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    // Last resort: Math.random
    for (let i = 0; i < 16; i++) {
      bytes[i] = (Math.random() * 256) | 0;
    }
  }

  // Set version bit (v4) and variant bit (RFC 4122)
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join("-");
}
