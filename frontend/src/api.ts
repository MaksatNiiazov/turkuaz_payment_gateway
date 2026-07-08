import type {
  AccessEvent,
  DynamicQrResponse,
  OneCPaymentExportEvent,
  PaymentProvider,
  PrintQrCodeConfigItem,
  QueueStatus,
  TigerInvoiceExportEvent,
  TransactionRow,
  WebhookEvent,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";
const IDENTITY_API_BASE_URL = import.meta.env.VITE_IDENTITY_API_BASE_URL || "/identity-api";
const IDENTITY_API_FALLBACK_BASE_URL =
  import.meta.env.VITE_IDENTITY_API_FALLBACK_BASE_URL || `${localApiUrl(8500)}/api/v1`;
const TOKEN_KEY = "identity_access_token";
const FALLBACK_TOKEN_KEY = "access_token";

type ListFilters = {
  limit: number;
  status?: string;
  provider?: string;
  invoice_id?: string;
};

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    const message = data?.detail || data?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

function params(values: Record<string, string | number | readonly string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== "") search.append(key, item);
      }
    } else if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  return search.toString();
}

export function fetchTransactions(filters: ListFilters): Promise<TransactionRow[]> {
  return requestJson<TransactionRow[]>(
    `/api/v1/local/transactions?${params(filters)}`,
  );
}

export function fetchInvoiceTransactions(invoiceId: string, limit = 100): Promise<TransactionRow[]> {
  return fetchTransactions({ limit, invoice_id: invoiceId });
}

export function fetchWebhooks(limit: number): Promise<WebhookEvent[]> {
  return requestJson<WebhookEvent[]>(
    `/api/v1/local/webhooks?${params({ limit })}`,
  );
}

export function fetchAccessEvents(limit: number): Promise<AccessEvent[]> {
  return requestJson<AccessEvent[]>(
    `/api/v1/local/access-events?${params({ limit })}`,
  );
}

export function cancelTransaction(transactionId: string): Promise<{ transaction_id: string; message: string }> {
  return requestJson<{ transaction_id: string; message: string }>(
    `/api/v1/local/transactions/${encodeURIComponent(transactionId)}/cancel`,
    { method: "PUT", body: "" },
  );
}

export function refreshTransaction(transactionId: string): Promise<TransactionRow> {
  return requestJson<TransactionRow>(
    `/api/v1/local/transactions/${encodeURIComponent(transactionId)}/refresh`,
    { method: "PUT", body: "" },
  );
}

export function createDemoDynamicQr(payload: {
  provider?: PaymentProvider;
  amount: number;
  branch?: number;
  cashier?: number;
  invoice_number?: string;
  source?: string;
  payer_code?: string;
  payer_full_name?: string;
  metadata?: Record<string, string>;
  is_long_living?: boolean;
}): Promise<DynamicQrResponse> {
  const metadata: Record<string, string> = { ...(payload.metadata ?? {}) };
  if (payload.invoice_number) metadata.invoice_number = payload.invoice_number;
  if (payload.source) metadata.source = payload.source;
  if (payload.payer_code) metadata.payer_code = payload.payer_code;
  if (payload.payer_full_name) metadata.payer_full_name = payload.payer_full_name;
  const query = params({ provider: payload.provider });

  return requestJson<DynamicQrResponse>(`/api/v1/admin/qr/dynamic${query ? `?${query}` : ""}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      amount: payload.amount,
      branch: payload.branch,
      cashier: payload.cashier,
      is_long_living: payload.is_long_living,
      metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
    }),
  });
}

export function fetchPrintQrCodes(): Promise<PrintQrCodeConfigItem[]> {
  return requestJson<PrintQrCodeConfigItem[]>("/api/v1/admin/print-qr-codes");
}

export function fetchTigerInvoiceEvents(filters: {
  limit: number;
  status?: QueueStatus[];
}): Promise<TigerInvoiceExportEvent[]> {
  return requestJson<TigerInvoiceExportEvent[]>(
    `/api/v1/local/tiger/invoice-events?${params(filters)}`,
  );
}

export function resetTigerInvoiceEvent(eventId: number): Promise<TigerInvoiceExportEvent> {
  return requestJson<TigerInvoiceExportEvent>(
    `/api/v1/local/tiger/invoice-events/${eventId}/reset`,
    { method: "POST", body: "" },
  );
}

export function fetchOneCPaymentEvents(filters: {
  limit: number;
  status?: QueueStatus[];
}): Promise<OneCPaymentExportEvent[]> {
  return requestJson<OneCPaymentExportEvent[]>(
    `/api/v1/local/1c/payment-events?${params(filters)}`,
  );
}

export function resetOneCPaymentEvent(eventId: number): Promise<OneCPaymentExportEvent> {
  return requestJson<OneCPaymentExportEvent>(
    `/api/v1/local/1c/payment-events/${eventId}/reset`,
    { method: "POST", body: "" },
  );
}

export function savePrintQrCodes(items: PrintQrCodeConfigItem[]): Promise<PrintQrCodeConfigItem[]> {
  return requestJson<PrintQrCodeConfigItem[]>("/api/v1/admin/print-qr-codes", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ items }),
  });
}

export function qrImageUrl(data: string): string {
  return `${API_BASE_URL}/api/v1/admin/qr/render?data=${encodeURIComponent(data)}`;
}

export function getToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY) || window.localStorage.getItem(FALLBACK_TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(FALLBACK_TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(FALLBACK_TOKEN_KEY);
}

export async function loginViaIdentity(email: string, password: string): Promise<void> {
  if (!email.trim().includes("@")) {
    throw new Error("Введите email пользователя из Identity.");
  }
  const data = await requestIdentityJson<{ access_token: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setToken(data.access_token);
}

async function requestIdentityJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const bases = uniqueBaseUrls([IDENTITY_API_BASE_URL, IDENTITY_API_FALLBACK_BASE_URL]);
  let lastError: Error | null = null;

  for (const baseUrl of bases) {
    try {
      return await requestIdentityJsonFromBase<T>(baseUrl, path, init);
    } catch (error) {
      if (!shouldRetryIdentityRequest(error) || baseUrl === bases[bases.length - 1]) {
        throw error;
      }
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  throw lastError ?? new Error("Identity API request failed");
}

async function requestIdentityJsonFromBase<T>(
  baseUrl: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const url = `${baseUrl}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      `Identity API недоступен по ${url}. Проверьте адрес, порт и CORS. ${message}`,
    );
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) clearToken();
    const message = identityErrorMessage(response.status, data);
    throw new HttpError(
      response.status,
      response.status === 401 ? `${message}. Endpoint: ${url}` : message,
    );
  }
  if (!isJsonObject(data)) {
    throw new Error(`Identity returned non-JSON response from ${baseUrl}`);
  }
  return data as T;
}

function uniqueBaseUrls(values: string[]): string[] {
  return values.filter((value, index) => value && values.indexOf(value) === index);
}

function shouldRetryIdentityRequest(error: unknown): boolean {
  return (
    !(error instanceof HttpError) ||
    error.status === 404 ||
    error.status === 405 ||
    error.status >= 500
  );
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function identityErrorMessage(status: number, data: unknown): string {
  const detail = isJsonObject(data) ? data.detail : null;
  const message = isJsonObject(data) ? data.message : null;
  if (typeof detail === "string" && detail) return detail;
  if (typeof message === "string" && message) return message;
  if (Array.isArray(detail)) {
    const firstMessage = detail
      .map((item) => (isJsonObject(item) && typeof item.msg === "string" ? item.msg : null))
      .find((item): item is string => Boolean(item));
    if (firstMessage) return firstMessage;
  }
  return `Identity вернул HTTP ${status}`;
}

function localApiUrl(port: number): string {
  if (typeof window === "undefined") return `http://localhost:${port}`;
  return `${window.location.protocol}//${window.location.hostname}:${port}`;
}

class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
