import type {
  AccessEvent,
  DynamicQrResponse,
  PaymentProvider,
  TransactionRow,
  WebhookEvent,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

type ListFilters = {
  limit: number;
  status?: string;
  provider?: string;
};

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...init.headers,
    },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = data?.detail || data?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

function params(values: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}

export function fetchTransactions(filters: ListFilters): Promise<TransactionRow[]> {
  return requestJson<TransactionRow[]>(
    `/api/v1/local/transactions?${params(filters)}`,
  );
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

export function qrImageUrl(data: string): string {
  return `${API_BASE_URL}/api/v1/admin/qr/render?data=${encodeURIComponent(data)}`;
}
