export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
  async binary(path: string): Promise<ArrayBuffer> {
    const response = await fetch(path);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        detail = (await response.json()).detail;
      } catch {
        // not JSON
      }
      throw new ApiError(response.status, detail);
    }
    return response.arrayBuffer();
  },
  async text(path: string): Promise<string> {
    const response = await fetch(path);
    return response.text();
  },
};

export function fileUrl(showId: string, path: string): string {
  return `/api/shows/${encodeURIComponent(showId)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}
