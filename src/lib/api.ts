const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

function buildUrl(endpoint: string): string {
  if (/^https?:\/\//i.test(endpoint)) {
    return endpoint;
  }

  const normalizedEndpoint = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
  return `${API_BASE}${normalizedEndpoint}`;
}

export async function apiFetch<T = unknown>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = buildUrl(endpoint);

  const defaultHeaders = {
    "Content-Type": "application/json",
  };

  const headers = new Headers(defaultHeaders);
  const incomingHeaders = new Headers(options.headers ?? {});
  incomingHeaders.forEach((value, key) => {
    headers.set(key, value);
  });

  const response = await fetch(url, {
    ...options,
    headers,
  });

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const responseBody = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      (isJson && responseBody && typeof responseBody === "object" && "detail" in responseBody
        ? String((responseBody as { detail?: unknown }).detail)
        : typeof responseBody === "string"
        ? responseBody
        : `Request failed with status ${response.status}`) ||
      `Request failed with status ${response.status}`;

    throw new Error(message);
  }

  return responseBody as T;
}
