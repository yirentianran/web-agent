import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("Vite config proxy settings", () => {
  function getViteConfigContent(): string {
    const configPath = resolve(__dirname, "../../vite.config.ts");
    return readFileSync(configPath, "utf-8");
  }

  it("has changeOrigin enabled for /ws proxy (Windows compatibility)", () => {
    const content = getViteConfigContent();

    // The /ws proxy block must contain changeOrigin: true
    // Without this, the Host header mismatch causes WS upgrade failures on Windows
    // Note: Use [\s\S]*? instead of [^}]+ to handle template literals with }
    const wsBlockMatch = content.match(/['"]\/ws['"]\s*:\s*\{([\s\S]*?)changeOrigin[\s\S]*?\n\s*\}/);
    expect(wsBlockMatch).not.toBeNull();

    // Verify both ws:true and changeOrigin:true are present
    expect(content).toMatch(/['"]\/ws['"][\s\S]*?ws:\s*true/);
    expect(content).toMatch(/['"]\/ws['"][\s\S]*?changeOrigin:\s*true/);
  });

  it("has changeOrigin enabled for /api proxy", () => {
    const content = getViteConfigContent();

    const apiBlockMatch = content.match(/['"]\/api['"]\s*:\s*\{([^}]+)\}/s);
    expect(apiBlockMatch).not.toBeNull();
    const apiBlock = apiBlockMatch![1];

    expect(apiBlock).toContain("changeOrigin");
    expect(apiBlock).toContain("true");
  });

  it("has changeOrigin enabled for /health proxy", () => {
    const content = getViteConfigContent();

    const healthBlockMatch = content.match(/['"]\/health['"]\s*:\s*\{([^}]+)\}/s);
    expect(healthBlockMatch).not.toBeNull();
    const healthBlock = healthBlockMatch![1];

    expect(healthBlock).toContain("changeOrigin");
    expect(healthBlock).toContain("true");
  });

  it("uses IPv4-compatible backend host (127.0.0.1 over localhost)", () => {
    const content = getViteConfigContent();

    // The backendHost default should be 127.0.0.1, not localhost,
    // because localhost may resolve to IPv6 (::1) on Windows while
    // uvicorn binds to IPv4 only.
    const hostDefaultMatch = content.match(/backendHost\s*=\s*env\.BACKEND_HOST\s*\?\?['"]([^'"]+)['"]/);
    if (hostDefaultMatch) {
      expect(hostDefaultMatch[1]).not.toBe("localhost");
    }
  });

  it("has explicit IPv4 host binding for dev server (Windows compatibility)", () => {
    const content = getViteConfigContent();

    // The server.host must be '127.0.0.1' (IPv4), not 'localhost' or undefined.
    // Windows resolves localhost to IPv6 (::1) first, causing WebSocket proxy
    // failures when the browser connects via IPv6 but backend only listens on IPv4.
    const serverBlockMatch = content.match(/server:\s*\{([\s\S]*?)\n\s*\}/);
    expect(serverBlockMatch).not.toBeNull();

    // Must have explicit host: '127.0.0.1'
    expect(serverBlockMatch![1]).toMatch(/host:\s*['"]127\.0\.0\.1['"]/);
  });
});
