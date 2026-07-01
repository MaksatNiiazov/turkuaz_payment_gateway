export type PaymentProvider = "mkassa" | "odengi";

export type TransactionRow = {
  id: string;
  provider: string;
  status: string | null;
  transaction_type: string | null;
  amount: number | null;
  branch: string | null;
  cashier: string | null;
  external_invoice_id: string | null;
  created_at: string | null;
  paid_at: string | null;
  payment_token: string | null;
  static_qr_link: string | null;
  metadata: Record<string, string> | null;
  raw_payload: Record<string, unknown>;
  updated_at: string;
};

export type WebhookEvent = {
  id: number;
  provider: string;
  transaction_id: string;
  status: string | null;
  payload: Record<string, unknown>;
  received_at: string;
};

export type AccessEvent = {
  id: number;
  integration_name: string;
  method: string;
  path: string;
  status_code: number | null;
  user_agent: string | null;
  remote_addr: string | null;
  created_at: string;
};

export type DynamicQrResponse = {
  id: string;
  amount: number | null;
  status: string | null;
  transaction_type: string | null;
  created_at: string | null;
  branch: string | number | null;
  cashier: string | number | null;
  paid_at: string | null;
  metadata: Record<string, string> | null;
  payment_token: string;
  provider_transaction_id?: string | number | null;
  invoice_id?: string | number | null;
  qr?: string | null;
  emv_qr?: string | null;
  qr_url?: string | null;
  link_app?: string | null;
  site_pay?: string | null;
};

export type PrintQrCodeConfigItem = {
  code: string;
  label: string;
  provider: PaymentProvider;
  enabled: boolean;
  slot: number;
  sort_order: number;
  tiger_bank_account_code: string | null;
};

export type TigerInvoiceExportEvent = {
  id: number;
  invoice_id: string;
  invoice_number: string | null;
  paid_transaction_id: string;
  paid_provider: string;
  provider_payment_id: string | null;
  target_bank_code: string | null;
  target_bank_account_code: string | null;
  amount: number | null;
  currency: string | null;
  status: string;
  event_payload: Record<string, unknown>;
  tiger_logical_ref: string | null;
  tiger_fiche_no: string | null;
  error_message: string | null;
  attempt_count: number;
  last_attempt_at: string | null;
  exported_at: string | null;
  created_at: string;
  updated_at: string;
};

export type OneCPaymentExportEvent = {
  id: number;
  payment_id: string;
  invoice_id: string;
  invoice_number: string | null;
  payment_code: string | null;
  paid_provider: string;
  provider_payment_id: string | null;
  amount: number | null;
  currency: string | null;
  status: string;
  event_payload: Record<string, unknown>;
  one_c_document_id: string | null;
  error_message: string | null;
  attempt_count: number;
  last_attempt_at: string | null;
  exported_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ViewMode =
  | "transactions"
  | "webhooks"
  | "invoices"
  | "queues"
  | "access"
  | "qr-demo"
  | "print-settings";
