import axios, { AxiosError, type AxiosRequestConfig } from "axios";

import webConfig from "@/constants/common-env";
import { clearStoredAuthSession, getStoredAuthKey } from "@/store/auth";

type RequestConfig = AxiosRequestConfig & {
  redirectOnUnauthorized?: boolean;
};

type ErrorPayload = {
  detail?: string | { error?: string | { message?: string } };
  error?: string | { message?: string };
  message?: string;
};

function errorMessageFromValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (!value || typeof value !== "object") {
    return "";
  }

  const item = value as { error?: unknown; message?: unknown };
  if (typeof item.message === "string") {
    return item.message;
  }
  return errorMessageFromValue(item.error);
}

export const request = axios.create({
  baseURL: webConfig.apiUrl.replace(/\/$/, ""),
});

request.interceptors.request.use(async (config) => {
  const authKey = await getStoredAuthKey();
  if (authKey && !config.headers.has("Authorization"))
    config.headers.set("Authorization", "Bearer " + authKey);
  return config;
});

export class HttpRequestError extends Error {
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "HttpRequestError";
    this.status = status;
  }
}

request.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ErrorPayload>) => {
    const status = error.response?.status;
    const shouldRedirect =
      (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;
    if (status === 401 && shouldRedirect && typeof window !== "undefined") {
      // Avoid redirect loop — only redirect if not already on /login
      if (!window.location.pathname.startsWith("/login")) {
        await clearStoredAuthSession();
        window.location.replace("/login");
      }
    }

    const payload = error.response?.data;
    const message =
      errorMessageFromValue(payload?.detail) ||
      errorMessageFromValue(payload?.error) ||
      payload?.message ||
      error.message ||
      `请求失败 (${status || 500})`;
    return Promise.reject(new HttpRequestError(message, status));
  },
);

type RequestOptions = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  redirectOnUnauthorized?: boolean;
  signal?: AbortSignal;
  timeout?: number;
  responseType?: AxiosRequestConfig["responseType"];
};

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
  const {
    method = "GET",
    body,
    headers,
    redirectOnUnauthorized = true,
    signal,
    timeout,
    responseType,
  } = options;
  const config: RequestConfig = {
    url: path,
    method,
    data: body,
    headers,
    redirectOnUnauthorized,
    signal,
    timeout,
    responseType,
  };
  const response = await request.request<T>(config);
  return response.data;
}

/** Incremental transport sharing the ordinary request's session policy. */
export async function streamRequest(path: string, body: unknown, signal: AbortSignal) {
  const authKey = await getStoredAuthKey();
  const response = await fetch(webConfig.apiUrl.replace(/\/$/, "") + path, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(authKey ? { Authorization: "Bearer " + authKey } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    if (response.status === 401 && !window.location.pathname.startsWith("/login")) {
      await clearStoredAuthSession();
      window.location.replace("/login");
    }
    const raw = await response.text();
    let message = raw;
    try {
      const payload = JSON.parse(raw);
      message =
        errorMessageFromValue(payload.detail) ||
        errorMessageFromValue(payload.error) ||
        payload.message ||
        raw;
    } catch {
      /* Non-JSON errors are summarized on the creative surface. */
    }
    throw new HttpRequestError(message || "请求失败 (" + response.status + ")", response.status);
  }
  return response;
}
