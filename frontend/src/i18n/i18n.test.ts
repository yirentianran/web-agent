import { describe, it, expect } from "vitest";
import en from "./en.json";
import zh from "./zh.json";

function collectKeys(obj: Record<string, unknown>, prefix = ""): string[] {
  const keys: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !("_plural" in (value as object))) {
      keys.push(...collectKeys(value as Record<string, unknown>, fullKey));
    } else {
      keys.push(fullKey);
    }
  }
  return keys;
}

describe("i18n key parity", () => {
  it("zh.json has all keys from en.json", () => {
    const enKeys = collectKeys(en as Record<string, unknown>);
    const zhKeys = collectKeys(zh as Record<string, unknown>);
    const missing = enKeys.filter((k) => !zhKeys.includes(k));
    expect(missing).toEqual([]);
  });

  it("en.json has all keys from zh.json (no extra keys in zh)", () => {
    const enKeys = collectKeys(en as Record<string, unknown>);
    const zhKeys = collectKeys(zh as Record<string, unknown>);
    const extra = zhKeys.filter((k) => !enKeys.includes(k));
    expect(extra).toEqual([]);
  });
});