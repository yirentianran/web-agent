/** CSRF token helper. The access_token cookie is httpOnly (sent automatically),
 * but the csrf_token cookie is readable by JS and must be included as a header
 * for state-changing requests.
 */

export function getCSRFToken(): string {
  const match = document.cookie.match(/csrf_token=([^;]+)/);
  return match ? match[1] : "";
}

export function csrfHeaders(): Record<string, string> {
  const csrf = getCSRFToken();
  return csrf ? { "X-CSRF-Token": csrf } : {};
}

/**
 * Fetch wrapper that sends credentials (cookies) and includes CSRF token
 * for state-changing methods. Use instead of raw fetch() for API calls.
 */
export async function apiFetch(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers: Record<string, string> = {};

  // Copy existing headers
  if (options.headers) {
    if (options.headers instanceof Headers) {
      options.headers.forEach((value, key) => {
        headers[key] = value;
      });
    } else if (Array.isArray(options.headers)) {
      for (const [k, v] of options.headers) {
        headers[k] = v;
      }
    } else {
      Object.assign(headers, options.headers);
    }
  }

  // Add CSRF token for state-changing methods
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = getCSRFToken();
  }

  return fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
  });
}

/** Fetch JSON with CSRF protection. Redirects to login on 401. */
export async function fetchJson<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const resp = await apiFetch(url, { ...options, method: options.method || "GET" });
  if (resp.status === 401) {
    localStorage.removeItem("userId");
    window.location.href = window.location.origin;
    throw new Error("Authentication required");
  }
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  }
  return resp.json() as Promise<T>;
}

/** POST JSON with CSRF protection. */
export async function postJson<T>(
  url: string,
  body?: unknown,
): Promise<T> {
  const options: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  return fetchJson<T>(url, options);
}
