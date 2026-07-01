import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AppShell,
  fetchServiceRegistry,
  Icon,
  readAccessClaims,
  serviceLinksFromRegistry,
} from "@turkuaz/ui";
import type { ServiceRegistryItem, UserConfig } from "@turkuaz/ui";
import {
  cancelTransaction,
  clearToken,
  createDemoDynamicQr,
  fetchAccessEvents,
  fetchInvoiceTransactions,
  fetchOneCPaymentEvents,
  fetchPrintQrCodes,
  fetchTigerInvoiceEvents,
  fetchTransactions,
  fetchWebhooks,
  getToken,
  loginViaIdentity,
  qrImageUrl,
  refreshTransaction,
  resetOneCPaymentEvent,
  resetTigerInvoiceEvent,
  savePrintQrCodes,
} from "./api";
import type {
  AccessEvent,
  DynamicQrResponse,
  OneCPaymentExportEvent,
  PaymentProvider,
  PrintQrCodeConfigItem,
  TigerInvoiceExportEvent,
  TransactionRow,
  ViewMode,
  WebhookEvent,
} from "./types";

const IDENTITY_API_BASE_URL = import.meta.env.VITE_IDENTITY_API_BASE_URL || "/identity-api";
const API_DOCS_URL = backendUrl(8502, "/docs");
const TOKEN_STORAGE_KEYS = ["identity_access_token", "access_token"];

type LoadState = {
  loading: boolean;
  error: string | null;
};

type CurrentIdentityUser = {
  email: string;
  full_name: string;
  branch_name: string | null;
  roles: string[];
  permissions?: string[];
  branch_permissions?: Record<string, string[]>;
  branch_permissions_by_id?: Record<string, string[]>;
  active_branch_id?: number | null;
  branch_id?: number | null;
  branch_code?: string | null;
};

const FIXED_PRINT_QR_CODE_TEMPLATES: PrintQrCodeConfigItem[] = [
  { code: "mbank", label: "MBank", provider: "mkassa", enabled: true, slot: 1, sort_order: 10, tiger_bank_account_code: null },
  { code: "obank", label: "О!Банк", provider: "odengi", enabled: true, slot: 2, sort_order: 20, tiger_bank_account_code: null },
  { code: "qr_3", label: "QR 3", provider: "mkassa", enabled: false, slot: 3, sort_order: 30, tiger_bank_account_code: null },
  { code: "qr_4", label: "QR 4", provider: "odengi", enabled: false, slot: 4, sort_order: 40, tiger_bank_account_code: null },
];

function fixedPrintQrCodes(items: PrintQrCodeConfigItem[]): PrintQrCodeConfigItem[] {
  const byCode = new Map(items.map((item) => [item.code, item]));
  return FIXED_PRINT_QR_CODE_TEMPLATES.map((template) => ({
    ...template,
    ...byCode.get(template.code),
    code: template.code,
  }));
}

function statusTone(status?: string | null): string {
  switch ((status || "").toLowerCase()) {
    case "paid":
    case "success":
      return "good";
    case "failed":
    case "canceled":
    case "overdue":
    case "error":
      return "bad";
    case "inited":
    case "waiting":
    case "qr_scanned":
    case "pending":
    case "processing":
      return "wait";
    default:
      return "muted";
  }
}

function formatAmount(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "-";
  return `${(value / 100).toLocaleString("ru-RU", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} сом`;
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function truncate(value: string, max = 20): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function canCancelTransaction(transaction: TransactionRow | null): boolean {
  if (!transaction) return false;
  return (
    transaction.transaction_type === "qr" &&
    ["inited", "waiting", "qr_scanned"].includes(transaction.status || "")
  );
}

function invoicePaymentMetrics(rows: TransactionRow[]) {
  const paid = rows.filter((item) => item.status === "paid").length;
  const active = rows.filter((item) =>
    ["inited", "waiting", "qr_scanned"].includes(item.status || ""),
  ).length;
  const canceled = rows.filter((item) => item.status === "canceled").length;
  const invoiceAmount = rows.find((item) => item.status === "paid")?.amount ?? rows[0]?.amount ?? null;
  return { active, canceled, invoiceAmount, paid };
}

function invoiceLabel(transaction: TransactionRow): string {
  return (
    transaction.external_invoice_id ||
    transaction.metadata?.invoice_id ||
    transaction.metadata?.order_id ||
    transaction.metadata?.invoice_number ||
    "-"
  );
}

type TransactionGroup = {
  key: string;
  label: string;
  hasBusinessKey: boolean;
  invoiceNumber: string | null;
  latestUpdated: string;
  metrics: ReturnType<typeof invoicePaymentMetrics>;
  paidProvider: string | null;
  providers: string[];
  rows: TransactionRow[];
};

function groupTransactionsByPayment(rows: TransactionRow[]): TransactionGroup[] {
  const groups = new Map<string, TransactionRow[]>();
  for (const row of rows) {
    const businessKey = invoiceLabel(row);
    const key = businessKey === "-" ? `transaction:${row.id}` : `payment:${businessKey}`;
    const groupRows = groups.get(key);
    if (groupRows) {
      groupRows.push(row);
    } else {
      groups.set(key, [row]);
    }
  }

  return [...groups.entries()]
    .map(([key, groupRows]) => {
      const hasBusinessKey = key.startsWith("payment:");
      const metrics = invoicePaymentMetrics(groupRows);
      const paidTransaction = groupRows.find((item) => item.status === "paid") ?? null;
      const latestUpdated = groupRows
        .map((item) => item.updated_at)
        .sort((left, right) => right.localeCompare(left))[0];
      const providers = [...new Set(groupRows.map((item) => providerLabel(item.provider)))];
      return {
        key,
        label: hasBusinessKey ? key.replace("payment:", "") : "Без счета 1С",
        hasBusinessKey,
        invoiceNumber:
          groupRows.map((item) => item.metadata?.invoice_number).find((value): value is string => Boolean(value)) ??
          null,
        latestUpdated,
        metrics,
        paidProvider: paidTransaction ? providerLabel(paidTransaction.provider) : null,
        providers,
        rows: groupRows,
      };
    })
    .sort((left, right) => right.latestUpdated.localeCompare(left.latestUpdated));
}

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getToken()));
  const isLoginPath = typeof window !== "undefined" && window.location.pathname === "/login";
  const [view, setView] = useState<ViewMode>("transactions");
  const [limit, setLimit] = useState(50);
  const [statusFilter, setStatusFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [transactions, setTransactions] = useState<TransactionRow[]>([]);
  const [invoiceId, setInvoiceId] = useState("");
  const [invoicePayments, setInvoicePayments] = useState<TransactionRow[]>([]);
  const [invoiceState, setInvoiceState] = useState<LoadState>({ loading: false, error: null });
  const [queueStatusFilter, setQueueStatusFilter] = useState("");
  const [tigerQueue, setTigerQueue] = useState<TigerInvoiceExportEvent[]>([]);
  const [oneCQueue, setOneCQueue] = useState<OneCPaymentExportEvent[]>([]);
  const [queueState, setQueueState] = useState<LoadState>({ loading: false, error: null });
  const [resettingQueueKey, setResettingQueueKey] = useState<string | null>(null);
  const [webhooks, setWebhooks] = useState<WebhookEvent[]>([]);
  const [accessEvents, setAccessEvents] = useState<AccessEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [state, setState] = useState<LoadState>({ loading: false, error: null });
  const [cancelingId, setCancelingId] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [qrResult, setQrResult] = useState<DynamicQrResponse | null>(null);
  const [qrState, setQrState] = useState<LoadState>({ loading: false, error: null });
  const [printQrCodes, setPrintQrCodes] = useState<PrintQrCodeConfigItem[]>([]);
  const [printSettingsState, setPrintSettingsState] = useState<LoadState>({
    loading: false,
    error: null,
  });
  const [registeredServices, setRegisteredServices] = useState<ServiceRegistryItem[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentIdentityUser | null>(null);

  const selectedTransaction = useMemo(
    () => transactions.find((item) => item.id === selectedId) ?? transactions[0] ?? null,
    [selectedId, transactions],
  );

  const metrics = useMemo(() => {
    const paid = transactions.filter((item) => item.status === "paid").length;
    const waiting = transactions.filter((item) =>
      ["inited", "waiting", "qr_scanned"].includes(item.status || ""),
    ).length;
    return [
      { label: "Транзакции", value: transactions.length, icon: "banknote" as const },
      { label: "Paid", value: paid, icon: "shield" as const },
      { label: "В ожидании", value: waiting, icon: "activity" as const },
      { label: "Webhook", value: webhooks.length, icon: "webhook" as const },
    ];
  }, [transactions, webhooks]);

  const invoiceMetrics = useMemo(() => invoicePaymentMetrics(invoicePayments), [invoicePayments]);

  const loadData = useCallback(async () => {
    setState({ loading: true, error: null });
    try {
      const [transactionRows, webhookRows, accessRows] = await Promise.all([
        fetchTransactions(
          {
            limit,
            status: statusFilter.trim() || undefined,
            provider: providerFilter.trim() || undefined,
          },
        ),
        fetchWebhooks(limit),
        fetchAccessEvents(limit),
      ]);

      setTransactions(transactionRows);
      setWebhooks(webhookRows);
      setAccessEvents(accessRows);
      setSelectedId((current) =>
        transactionRows.some((row) => row.id === current) ? current : transactionRows[0]?.id ?? null,
      );
      setState({ loading: false, error: null });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  }, [limit, providerFilter, statusFilter]);

  const loadPrintSettings = useCallback(async () => {
    setPrintSettingsState({ loading: true, error: null });
    try {
      const rows = await fetchPrintQrCodes();
      setPrintQrCodes(rows);
      setPrintSettingsState({ loading: false, error: null });
    } catch (error) {
      setPrintSettingsState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, []);

  const loadInvoicePayments = useCallback(async () => {
    const trimmedInvoiceId = invoiceId.trim();
    if (!trimmedInvoiceId) {
      setInvoicePayments([]);
      setInvoiceState({ loading: false, error: "Введите ID счета или документа из 1С." });
      return;
    }

    setInvoiceState({ loading: true, error: null });
    try {
      const rows = await fetchInvoiceTransactions(trimmedInvoiceId);
      setInvoicePayments(rows);
      setInvoiceState({ loading: false, error: null });
    } catch (error) {
      setInvoiceState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [invoiceId]);

  const loadQueues = useCallback(async () => {
    setQueueState({ loading: true, error: null });
    try {
      const status = queueStatusFilter.trim() || undefined;
      const [tigerRows, oneCRows] = await Promise.all([
        fetchTigerInvoiceEvents({ limit, status }),
        fetchOneCPaymentEvents({ limit, status }),
      ]);
      setTigerQueue(tigerRows);
      setOneCQueue(oneCRows);
      setQueueState({ loading: false, error: null });
    } catch (error) {
      setQueueState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }, [limit, queueStatusFilter]);

  useEffect(() => {
    if (!isAuthenticated || isLoginPath) return;
    if (view === "print-settings") {
      void loadPrintSettings();
    } else if (view === "invoices") {
      if (invoiceId.trim()) void loadInvoicePayments();
    } else if (view === "queues") {
      void loadQueues();
    } else if (view !== "qr-demo") {
      void loadData();
    }
  }, [invoiceId, isAuthenticated, isLoginPath, loadData, loadInvoicePayments, loadPrintSettings, loadQueues, view]);

  useEffect(() => {
    let cancelled = false;
    if (!isAuthenticated || isLoginPath || !getStoredIdentityToken()) {
      setCurrentUser(null);
      setRegisteredServices([]);
      return () => {
        cancelled = true;
      };
    }

    void Promise.all([
      fetchCurrentIdentityUser({
        identityApiBaseUrl: IDENTITY_API_BASE_URL,
        tokenStorageKeys: TOKEN_STORAGE_KEYS,
      }).catch(() => null),
      fetchServiceRegistry({
        identityApiBaseUrl: IDENTITY_API_BASE_URL,
        tokenStorageKeys: TOKEN_STORAGE_KEYS,
      }).catch(() => [] as ServiceRegistryItem[]),
    ])
      .then(([me, services]) => {
        if (!cancelled) {
          setCurrentUser(me);
          setRegisteredServices(services);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCurrentUser(null);
          setRegisteredServices([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, isLoginPath]);

  async function handleCancel(transaction: TransactionRow) {
    if (!canCancelTransaction(transaction) || cancelingId) return;
    const confirmed = window.confirm(`Отменить операцию ${transaction.id}?`);
    if (!confirmed) return;

    setCancelingId(transaction.id);
    setState({ loading: false, error: null });
    try {
      await cancelTransaction(transaction.id);
      await loadData();
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : String(error) });
    } finally {
      setCancelingId(null);
    }
  }

  async function handleRefreshStatus(transaction: TransactionRow) {
    if (refreshingId) return;
    setRefreshingId(transaction.id);
    setState({ loading: false, error: null });
    try {
      await refreshTransaction(transaction.id);
      await loadData();
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : String(error) });
    } finally {
      setRefreshingId(null);
    }
  }

  async function handleCreateDemoQr(payload: {
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
  }) {
    setQrState({ loading: true, error: null });
    try {
      const result = await createDemoDynamicQr(payload);
      setQrResult(result);
      setSelectedId(result.id);
      await loadData();
      setQrState({ loading: false, error: null });
    } catch (error) {
      setQrState({ loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  }

  async function handleSavePrintQrCodes(items: PrintQrCodeConfigItem[]) {
    setPrintSettingsState({ loading: true, error: null });
    try {
      const saved = await savePrintQrCodes(items);
      setPrintQrCodes(saved);
      setPrintSettingsState({ loading: false, error: null });
    } catch (error) {
      setPrintSettingsState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleResetTigerQueue(event: TigerInvoiceExportEvent) {
    const confirmed = window.confirm(`Вернуть событие Tiger #${event.id} в очередь?`);
    if (!confirmed || resettingQueueKey) return;

    setResettingQueueKey(`tiger:${event.id}`);
    setQueueState({ loading: false, error: null });
    try {
      await resetTigerInvoiceEvent(event.id);
      await loadQueues();
    } catch (error) {
      setQueueState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setResettingQueueKey(null);
    }
  }

  async function handleResetOneCQueue(event: OneCPaymentExportEvent) {
    const confirmed = window.confirm(`Вернуть событие 1С #${event.id} в очередь?`);
    if (!confirmed || resettingQueueKey) return;

    setResettingQueueKey(`1c:${event.id}`);
    setQueueState({ loading: false, error: null });
    try {
      await resetOneCPaymentEvent(event.id);
      await loadQueues();
    } catch (error) {
      setQueueState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setResettingQueueKey(null);
    }
  }

  async function handleInvoiceRefresh(transaction: TransactionRow) {
    if (refreshingId) return;
    setRefreshingId(transaction.id);
    setInvoiceState({ loading: false, error: null });
    try {
      await refreshTransaction(transaction.id);
      await loadInvoicePayments();
    } catch (error) {
      setInvoiceState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setRefreshingId(null);
    }
  }

  async function handleInvoiceCancel(transaction: TransactionRow) {
    if (!canCancelTransaction(transaction) || cancelingId) return;
    const confirmed = window.confirm(`Отменить оплату ${transaction.id} внутри инвойса?`);
    if (!confirmed) return;

    setCancelingId(transaction.id);
    setInvoiceState({ loading: false, error: null });
    try {
      await cancelTransaction(transaction.id);
      await loadInvoicePayments();
    } catch (error) {
      setInvoiceState({
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setCancelingId(null);
    }
  }

  const navItems = [
    {
      key: "transactions",
      label: "Транзакции",
      icon: "banknote" as const,
      active: view === "transactions",
      onClick: () => setView("transactions"),
    },
    {
      key: "webhooks",
      label: "Webhooks",
      icon: "webhook" as const,
      active: view === "webhooks",
      onClick: () => setView("webhooks"),
    },
    {
      key: "invoices",
      label: "Счета 1С",
      icon: "file" as const,
      active: view === "invoices",
      onClick: () => setView("invoices"),
    },
    {
      key: "queues",
      label: "Очереди",
      icon: "activity" as const,
      active: view === "queues",
      onClick: () => setView("queues"),
    },
    {
      key: "access",
      label: "Доступы",
      icon: "database" as const,
      active: view === "access",
      onClick: () => setView("access"),
    },
    {
      key: "qr-demo",
      label: "QR Demo",
      icon: "qr" as const,
      active: view === "qr-demo",
      onClick: () => setView("qr-demo"),
    },
    {
      key: "print-settings",
      label: "QR для 1С",
      icon: "database" as const,
      active: view === "print-settings",
      onClick: () => setView("print-settings"),
    },
  ];
  const pageTitle =
    view === "transactions"
      ? "Транзакции"
      : view === "webhooks"
        ? "Webhook события"
        : view === "access"
          ? "Доступы"
          : view === "queues"
          ? "Очереди"
          : view === "invoices"
          ? "Счет 1С"
          : view === "print-settings"
            ? "QR для 1С"
            : "QR Demo";
  const pageDescription =
    view === "invoices"
      ? "Один счет из 1С и все связанные оплаты в МБанк, О!Банк и других QR."
    : view === "queues"
      ? "Статусы отправки успешных оплат в Tiger и 1С."
    : view === "print-settings"
      ? "Банковские QR-коды для печатной формы 1С."
      : view === "qr-demo"
      ? "Создание тестового динамического QR через backend API."
      : "Операционная панель для просмотра платежей, callback'ов и обращений интеграций.";
  const shellClaims = currentUser ?? readAccessClaims(TOKEN_STORAGE_KEYS);
  const shellUser = currentUser
    ? userMenuFromIdentityUser(currentUser, handleLogout)
    : userMenuFromClaims(readAccessClaims(TOKEN_STORAGE_KEYS) as Record<string, unknown>, handleLogout);

  if (!isAuthenticated || isLoginPath) {
    return (
      <LoginPage
        onLoggedIn={() => {
          window.history.replaceState(null, "", "/");
          setIsAuthenticated(true);
        }}
      />
    );
  }

  return (
    <AppShell
      brand={{
        href: "/",
        mark: "T",
        title: "Turkuaz Payments",
        subtitle: "Payment Gateway",
      }}
      navItems={navItems}
      sideLinks={[
        ...serviceLinksFromRegistry(registeredServices, { currentServiceCode: "payments" }),
        { href: API_DOCS_URL, label: "Swagger", icon: "file" },
      ]}
      accessClaims={shellClaims}
      tokenStorageKeys={TOKEN_STORAGE_KEYS}
      serviceName="Payments"
      pageTitle={pageTitle}
      pageDescription={pageDescription}
      breadcrumbs={[{ label: "Payments" }, { label: pageTitle }]}
      headerActions={[
        {
          key: "refresh",
          label: "Обновить",
          icon: "refresh",
          onClick: () =>
            view === "print-settings"
              ? void loadPrintSettings()
              : view === "queues"
                ? void loadQueues()
              : view === "invoices"
                ? void loadInvoicePayments()
                : void loadData(),
        },
      ]}
      environment="local"
      version="v0.1.0"
      apiStatus={state.error || qrState.error || printSettingsState.error ? "degraded" : "online"}
      footerLinks={[{ href: API_DOCS_URL, label: "Swagger" }]}
      user={shellUser}
    >
        {view === "print-settings" ? (
          <PrintSettingsPanel
            items={printQrCodes}
            state={printSettingsState}
            onReload={() => void loadPrintSettings()}
            onSave={(items) => void handleSavePrintQrCodes(items)}
          />
        ) : view === "queues" ? (
          <QueuesPanel
            limit={limit}
            oneCEvents={oneCQueue}
            resettingKey={resettingQueueKey}
            state={queueState}
            statusFilter={queueStatusFilter}
            tigerEvents={tigerQueue}
            onLimitChange={setLimit}
            onRefresh={() => void loadQueues()}
            onResetOneC={(event) => void handleResetOneCQueue(event)}
            onResetTiger={(event) => void handleResetTigerQueue(event)}
            onStatusFilterChange={setQueueStatusFilter}
          />
        ) : view === "qr-demo" ? (
          <QrDemoPanel
            result={qrResult}
            state={qrState}
            onCreate={(payload) => void handleCreateDemoQr(payload)}
          />
        ) : view === "invoices" ? (
          <InvoicePanel
            cancelingId={cancelingId}
            invoiceId={invoiceId}
            metrics={invoiceMetrics}
            refreshingId={refreshingId}
            rows={invoicePayments}
            state={invoiceState}
            onCancel={(transaction) => void handleInvoiceCancel(transaction)}
            onInvoiceIdChange={setInvoiceId}
            onRefresh={() => void loadInvoicePayments()}
            onRefreshStatus={(transaction) => void handleInvoiceRefresh(transaction)}
          />
        ) : (
          <>
            <section className="metrics-grid">
              {metrics.map((metric) => (
                <div className="metric" key={metric.label}>
                  <Icon name={metric.icon} size={20} />
                  <span>{metric.label}</span>
                  <strong>{metric.value}</strong>
                </div>
              ))}
            </section>

            <section className="toolbar">
              <label>
                Лимит
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={limit}
                  onChange={(event) => setLimit(Number(event.target.value) || 50)}
                />
              </label>
              {view === "transactions" && (
                <>
                  <label>
                    Статус
                    <input
                      value={statusFilter}
                      placeholder="paid, inited..."
                      onChange={(event) => setStatusFilter(event.target.value)}
                    />
                  </label>
                  <label>
                    Provider
                    <input
                      value={providerFilter}
                      placeholder="mkassa"
                      onChange={(event) => setProviderFilter(event.target.value)}
                    />
                  </label>
                </>
              )}
              <button className="refresh" type="button" onClick={() => void loadData()}>
                <Icon name="refresh" size={16} />
                Обновить
              </button>
              <div className="toolbar-state">
                {state.loading ? "Загрузка..." : state.error ? state.error : "Данные актуальны"}
              </div>
            </section>

            <section className="content-grid">
              <div className="table-panel">
                {view === "transactions" && (
                  <TransactionsTable
                    rows={transactions}
                    selectedId={selectedTransaction?.id ?? null}
                    onSelect={setSelectedId}
                  />
                )}
                {view === "webhooks" && <WebhooksTable rows={webhooks} />}
                {view === "access" && <AccessTable rows={accessEvents} />}
              </div>
              {view === "transactions" && (
                <TransactionDetails
                  transaction={selectedTransaction}
                  cancelingId={cancelingId}
                  refreshingId={refreshingId}
                  onCancel={(transaction) => void handleCancel(transaction)}
                  onRefreshStatus={(transaction) => void handleRefreshStatus(transaction)}
                />
              )}
            </section>
          </>
        )}
    </AppShell>
  );
}

function TransactionsTable({
  rows,
  selectedId,
  onSelect,
}: {
  rows: TransactionRow[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) return <EmptyState />;
  const groups = groupTransactionsByPayment(rows);
  return (
    <table>
      <thead>
        <tr>
          <th>Платеж / операция</th>
          <th>Provider</th>
          <th>Status</th>
          <th>Type</th>
          <th>Amount</th>
          <th>Счет 1С</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group) => (
          <TransactionGroupRows
            group={group}
            key={group.key}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        ))}
      </tbody>
    </table>
  );
}

function TransactionGroupRows({
  group,
  selectedId,
  onSelect,
}: {
  group: TransactionGroup;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const showGroupHeader = group.hasBusinessKey && group.rows.length > 1;
  return (
    <>
      {showGroupHeader && (
        <tr className="transaction-group-row">
          <td colSpan={7}>
            <div className="transaction-group-summary">
              <div>
                <span className="summary-label">Счет 1С</span>
                <strong className="mono">{group.label}</strong>
              </div>
              <div>
                <span className="summary-label">Номер</span>
                <strong>{group.invoiceNumber || "-"}</strong>
              </div>
              <div>
                <span className="summary-label">Сумма</span>
                <strong>{formatAmount(group.metrics.invoiceAmount)}</strong>
              </div>
              <div>
                <span className="summary-label">Оплата</span>
                <strong>{group.paidProvider || "-"}</strong>
              </div>
              <div className="group-statuses">
                <span className="status good">paid {group.metrics.paid}</span>
                <span className="status wait">active {group.metrics.active}</span>
                <span className="status bad">canceled {group.metrics.canceled}</span>
              </div>
            </div>
          </td>
        </tr>
      )}
      {group.rows.map((row) => (
        <tr
          className={`${row.id === selectedId ? "selected" : ""} ${showGroupHeader ? "transaction-child-row" : ""}`}
          key={row.id}
          onClick={() => onSelect(row.id)}
        >
          <td className="mono">{truncate(row.id, 24)}</td>
          <td>{providerLabel(row.provider)}</td>
          <td><span className={`status ${statusTone(row.status)}`}>{row.status || "unknown"}</span></td>
          <td>{row.transaction_type || "-"}</td>
          <td>{formatAmount(row.amount)}</td>
          <td>{showGroupHeader ? row.metadata?.print_qr_code || "-" : invoiceLabel(row)}</td>
          <td>{formatDate(row.updated_at)}</td>
        </tr>
      ))}
    </>
  );
}

function WebhooksTable({ rows }: { rows: WebhookEvent[] }) {
  if (rows.length === 0) return <EmptyState />;
  return (
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Provider</th>
          <th>Transaction</th>
          <th>Status</th>
          <th>Received</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>{row.id}</td>
            <td>{row.provider}</td>
            <td className="mono">{truncate(row.transaction_id, 28)}</td>
            <td><span className={`status ${statusTone(row.status)}`}>{row.status || "unknown"}</span></td>
            <td>{formatDate(row.received_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AccessTable({ rows }: { rows: AccessEvent[] }) {
  if (rows.length === 0) return <EmptyState />;
  return (
    <table>
      <thead>
        <tr>
          <th>Integration</th>
          <th>Method</th>
          <th>Path</th>
          <th>Code</th>
          <th>Remote</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>{row.integration_name}</td>
            <td>{row.method}</td>
            <td className="mono">{row.path}</td>
            <td>{row.status_code || "-"}</td>
            <td>{row.remote_addr || "-"}</td>
            <td>{formatDate(row.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function InvoicePanel({
  invoiceId,
  rows,
  metrics,
  state,
  cancelingId,
  refreshingId,
  onInvoiceIdChange,
  onRefresh,
  onRefreshStatus,
  onCancel,
}: {
  invoiceId: string;
  rows: TransactionRow[];
  metrics: ReturnType<typeof invoicePaymentMetrics>;
  state: LoadState;
  cancelingId: string | null;
  refreshingId: string | null;
  onInvoiceIdChange: (value: string) => void;
  onRefresh: () => void;
  onRefreshStatus: (transaction: TransactionRow) => void;
  onCancel: (transaction: TransactionRow) => void;
}) {
  const paidTransaction = rows.find((item) => item.status === "paid");
  const invoiceNumber = rows
    .map((item) => item.metadata?.invoice_number)
    .find((value): value is string => Boolean(value));

  return (
    <section className="invoice-panel">
      <form
        className="toolbar invoice-search"
        onSubmit={(event) => {
          event.preventDefault();
          onRefresh();
        }}
      >
        <label>
          ID счета/документа из 1С
          <input
            autoComplete="off"
            placeholder="550e8400-e29b-41d4-a716-446655440000"
            value={invoiceId}
            onChange={(event) => onInvoiceIdChange(event.target.value)}
          />
        </label>
        <button className="refresh" disabled={state.loading} type="submit">
          <Icon name="search" size={16} />
          {state.loading ? "Поиск..." : "Показать оплаты"}
        </button>
        <div className="toolbar-state">
          {state.loading ? "Загрузка..." : state.error ? state.error : "Счет готов к проверке"}
        </div>
      </form>

      <section className="metrics-grid invoice-metrics">
        <div className="metric">
          <Icon name="banknote" size={20} />
          <span>Оплат</span>
          <strong>{rows.length}</strong>
        </div>
        <div className="metric">
          <Icon name="shield" size={20} />
          <span>Paid</span>
          <strong>{metrics.paid}</strong>
        </div>
        <div className="metric">
          <Icon name="activity" size={20} />
          <span>Активные</span>
          <strong>{metrics.active}</strong>
        </div>
        <div className="metric">
          <Icon name="ban" size={20} />
          <span>Отменены</span>
          <strong>{metrics.canceled}</strong>
        </div>
      </section>

      {rows.length > 0 && (
        <section className="invoice-summary">
          <div>
            <span className="summary-label">Счет 1С</span>
            <strong className="mono">{invoiceId.trim() || invoiceLabel(rows[0])}</strong>
          </div>
          <div>
            <span className="summary-label">Номер</span>
            <strong>{invoiceNumber || "-"}</strong>
          </div>
          <div>
            <span className="summary-label">Сумма</span>
            <strong>{formatAmount(metrics.invoiceAmount)}</strong>
          </div>
          <div>
            <span className="summary-label">Оплачено через</span>
            <strong>{paidTransaction ? providerLabel(paidTransaction.provider) : "-"}</strong>
          </div>
        </section>
      )}

      <div className="table-panel invoice-table-panel">
        {rows.length === 0 ? (
          <EmptyState />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Payment ID</th>
                <th>Status</th>
                <th>Amount</th>
                <th>QR</th>
                <th>Updated</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{providerLabel(row.provider)}</td>
                  <td className="mono">{truncate(row.id, 30)}</td>
                  <td><span className={`status ${statusTone(row.status)}`}>{row.status || "unknown"}</span></td>
                  <td>{formatAmount(row.amount)}</td>
                  <td>{row.metadata?.print_qr_code || "-"}</td>
                  <td>{formatDate(row.updated_at)}</td>
                  <td>
                    <div className="row-actions">
                      <button
                        className="icon-action"
                        disabled={refreshingId === row.id}
                        title="Обновить статус"
                        type="button"
                        onClick={() => onRefreshStatus(row)}
                      >
                        <Icon name="refresh" size={15} />
                      </button>
                      {canCancelTransaction(row) && (
                        <button
                          className="icon-action danger-icon"
                          disabled={cancelingId === row.id}
                          title="Отменить оплату"
                          type="button"
                          onClick={() => onCancel(row)}
                        >
                          <Icon name="ban" size={15} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function QueuesPanel({
  tigerEvents,
  oneCEvents,
  state,
  statusFilter,
  limit,
  resettingKey,
  onStatusFilterChange,
  onLimitChange,
  onRefresh,
  onResetTiger,
  onResetOneC,
}: {
  tigerEvents: TigerInvoiceExportEvent[];
  oneCEvents: OneCPaymentExportEvent[];
  state: LoadState;
  statusFilter: string;
  limit: number;
  resettingKey: string | null;
  onStatusFilterChange: (value: string) => void;
  onLimitChange: (value: number) => void;
  onRefresh: () => void;
  onResetTiger: (event: TigerInvoiceExportEvent) => void;
  onResetOneC: (event: OneCPaymentExportEvent) => void;
}) {
  const tigerPending = tigerEvents.filter((event) => event.status === "pending" || event.status === "error").length;
  const oneCPending = oneCEvents.filter((event) => event.status === "pending" || event.status === "error").length;
  const [activeQueue, setActiveQueue] = useState<"tiger" | "1c">("tiger");

  return (
    <section className="queues-panel">
      <section className="toolbar">
        <label>
          Лимит
          <input
            max={500}
            min={1}
            type="number"
            value={limit}
            onChange={(event) => onLimitChange(Number(event.target.value) || 50)}
          />
        </label>
        <label>
          Статус
          <select value={statusFilter} onChange={(event) => onStatusFilterChange(event.target.value)}>
            <option value="">Все</option>
            <option value="pending">pending</option>
            <option value="success">success</option>
            <option value="error">error</option>
          </select>
        </label>
        <button className="refresh" disabled={state.loading} type="button" onClick={onRefresh}>
          <Icon name="refresh" size={16} />
          {state.loading ? "Загрузка..." : "Обновить"}
        </button>
        <div className="toolbar-state">
          {state.loading ? "Загрузка..." : state.error ? state.error : "Очереди загружены"}
        </div>
      </section>

      <section className="metrics-grid invoice-metrics">
        <div className="metric">
          <Icon name="database" size={20} />
          <span>Tiger всего</span>
          <strong>{tigerEvents.length}</strong>
        </div>
        <div className="metric">
          <Icon name="activity" size={20} />
          <span>Tiger pending/error</span>
          <strong>{tigerPending}</strong>
        </div>
        <div className="metric">
          <Icon name="file" size={20} />
          <span>1С всего</span>
          <strong>{oneCEvents.length}</strong>
        </div>
        <div className="metric">
          <Icon name="activity" size={20} />
          <span>1С pending/error</span>
          <strong>{oneCPending}</strong>
        </div>
      </section>

      <section className="queue-tabs" aria-label="Очереди отправки">
        <button
          className={activeQueue === "tiger" ? "active" : ""}
          type="button"
          onClick={() => setActiveQueue("tiger")}
        >
          Tiger
          <span>{tigerPending}</span>
        </button>
        <button
          className={activeQueue === "1c" ? "active" : ""}
          type="button"
          onClick={() => setActiveQueue("1c")}
        >
          1С
          <span>{oneCPending}</span>
        </button>
      </section>

      <section className="queue-card">
        <div className="queue-card-header">
          <h2>{activeQueue === "tiger" ? "Очередь Tiger" : "Очередь 1С"}</h2>
        </div>
        <div className="queue-table-wrap">
          {activeQueue === "tiger" ? (
          <TigerQueueTable
            events={tigerEvents}
            resettingKey={resettingKey}
            onReset={onResetTiger}
          />
          ) : (
          <OneCQueueTable
            events={oneCEvents}
            resettingKey={resettingKey}
            onReset={onResetOneC}
          />
          )}
        </div>
      </section>
    </section>
  );
}

function TigerQueueTable({
  events,
  resettingKey,
  onReset,
}: {
  events: TigerInvoiceExportEvent[];
  resettingKey: string | null;
  onReset: (event: TigerInvoiceExportEvent) => void;
}) {
  if (events.length === 0) return <EmptyState />;
  return (
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Status</th>
          <th>Счет</th>
          <th>Оплата</th>
          <th>Банк Tiger</th>
          <th>Попытки</th>
          <th>Результат</th>
          <th>Ошибка</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <tr key={event.id}>
            <td className="mono">#{event.id}</td>
            <td><span className={`status ${statusTone(event.status)}`}>{event.status}</span></td>
            <td>
              <div>{event.invoice_number || "-"}</div>
              <div className="mono muted-inline">{truncate(event.invoice_id, 18)}</div>
            </td>
            <td>
              <div>{providerLabel(event.paid_provider)}</div>
              <div>{formatAmount(event.amount)}</div>
            </td>
            <td className="mono">{event.target_bank_account_code || event.target_bank_code || "-"}</td>
            <td>{event.attempt_count}</td>
            <td>
              <div>{event.tiger_fiche_no || "-"}</div>
              <div className="mono muted-inline">{event.tiger_logical_ref || ""}</div>
            </td>
            <td>{event.error_message ? truncate(event.error_message, 48) : "-"}</td>
            <td>
              <button
                className="icon-action"
                disabled={resettingKey === `tiger:${event.id}`}
                title="Вернуть в очередь Tiger"
                type="button"
                onClick={() => onReset(event)}
              >
                <Icon name="refresh" size={15} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OneCQueueTable({
  events,
  resettingKey,
  onReset,
}: {
  events: OneCPaymentExportEvent[];
  resettingKey: string | null;
  onReset: (event: OneCPaymentExportEvent) => void;
}) {
  if (events.length === 0) return <EmptyState />;
  return (
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Status</th>
          <th>Счет</th>
          <th>Оплата</th>
          <th>QR</th>
          <th>Попытки</th>
          <th>Документ 1С</th>
          <th>Ошибка</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
        {events.map((event) => (
          <tr key={event.id}>
            <td className="mono">#{event.id}</td>
            <td><span className={`status ${statusTone(event.status)}`}>{event.status}</span></td>
            <td>
              <div>{event.invoice_number || "-"}</div>
              <div className="mono muted-inline">{truncate(event.invoice_id, 18)}</div>
            </td>
            <td>
              <div>{providerLabel(event.paid_provider)}</div>
              <div>{formatAmount(event.amount)}</div>
            </td>
            <td>{event.payment_code || "-"}</td>
            <td>{event.attempt_count}</td>
            <td>{event.one_c_document_id || "-"}</td>
            <td>{event.error_message ? truncate(event.error_message, 48) : "-"}</td>
            <td>
              <button
                className="icon-action"
                disabled={resettingKey === `1c:${event.id}`}
                title="Вернуть в очередь 1С"
                type="button"
                onClick={() => onReset(event)}
              >
                <Icon name="refresh" size={15} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function providerLabel(provider: string): string {
  if (provider === "mkassa") return "MBank";
  if (provider === "odengi") return "О!Банк";
  return provider;
}

function TransactionDetails({
  transaction,
  cancelingId,
  refreshingId,
  onCancel,
  onRefreshStatus,
}: {
  transaction: TransactionRow | null;
  cancelingId: string | null;
  refreshingId: string | null;
  onCancel: (transaction: TransactionRow) => void;
  onRefreshStatus: (transaction: TransactionRow) => void;
}) {
  return (
    <aside className="details-panel">
      <div className="details-title">
        <Icon name="search" size={18} />
        <h2>Детали</h2>
      </div>
      {!transaction ? (
        <p className="empty-copy">Выберите транзакцию в таблице.</p>
      ) : (
        <>
          <dl>
            <dt>ID</dt>
            <dd className="mono">{transaction.id}</dd>
            <dt>Status</dt>
            <dd><span className={`status ${statusTone(transaction.status)}`}>{transaction.status}</span></dd>
            <dt>Branch / Cashier</dt>
            <dd>{transaction.branch || "-"} / {transaction.cashier || "-"}</dd>
            <dt>Created</dt>
            <dd>{formatDate(transaction.created_at)}</dd>
            <dt>Paid</dt>
            <dd>{formatDate(transaction.paid_at)}</dd>
          </dl>
          <button
            className="secondary-action detail-action"
            type="button"
            disabled={refreshingId === transaction.id}
            onClick={() => onRefreshStatus(transaction)}
          >
            <Icon name="refresh" size={15} />
            {refreshingId === transaction.id ? "Статус обновляется..." : "Обновить статус"}
          </button>
          {canCancelTransaction(transaction) && (
            <button
              className="danger-action detail-action"
              type="button"
              disabled={cancelingId === transaction.id}
              onClick={() => onCancel(transaction)}
            >
              <Icon name="ban" size={15} />
              {cancelingId === transaction.id ? "Операция отменяется..." : "Отменить операцию"}
            </button>
          )}
          <h3>Metadata</h3>
          <pre>{JSON.stringify(transaction.metadata || {}, null, 2)}</pre>
          <h3>Raw payload</h3>
          <pre>{JSON.stringify(transaction.raw_payload || {}, null, 2)}</pre>
        </>
      )}
    </aside>
  );
}

function PrintSettingsPanel({
  items,
  state,
  onReload,
  onSave,
}: {
  items: PrintQrCodeConfigItem[];
  state: LoadState;
  onReload: () => void;
  onSave: (items: PrintQrCodeConfigItem[]) => void;
}) {
  const [draft, setDraft] = useState<PrintQrCodeConfigItem[]>(fixedPrintQrCodes(items));

  useEffect(() => {
    setDraft(fixedPrintQrCodes(items));
  }, [items]);

  function updateItem(index: number, patch: Partial<PrintQrCodeConfigItem>) {
    setDraft((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)),
    );
  }

  function normalizedDraft(): PrintQrCodeConfigItem[] {
    return fixedPrintQrCodes(draft)
      .map((item, index) => ({
        ...item,
        label: item.label.trim(),
        tiger_bank_account_code: item.tiger_bank_account_code?.trim() || null,
        slot: Number(item.slot) || 1,
        sort_order: ((Number(item.slot) || 1) * 10) + index,
      }))
      .sort(
        (left, right) =>
          left.slot - right.slot || left.sort_order - right.sort_order || left.code.localeCompare(right.code),
      );
  }

  const enabledSlots = draft.filter((item) => item.enabled).map((item) => Number(item.slot));
  const hasDuplicateSlots = new Set(enabledSlots).size !== enabledSlots.length;
  const hasEmptyFields = draft.some((item) => !item.label.trim());
  const hasMissingTigerAccounts = draft.some(
    (item) => item.enabled && !item.tiger_bank_account_code?.trim(),
  );
  const canSave =
    !hasDuplicateSlots && !hasEmptyFields && !hasMissingTigerAccounts && !state.loading;

  return (
    <section className="settings-panel">
      <div className="settings-header">
        <div>
          <h2>Печатные QR-коды для 1С</h2>
          <p className="hint-copy">
            1С печатает только эти 4 фиксированных кода. Можно включать, менять подпись, provider и слот.
          </p>
        </div>
        <div className="settings-actions">
          <button className="secondary-action" disabled={state.loading} type="button" onClick={onReload}>
            <Icon name="refresh" size={15} />
            Обновить
          </button>
          <button className="refresh" disabled={!canSave} type="button" onClick={() => onSave(normalizedDraft())}>
            <Icon name="shield" size={15} />
            {state.loading ? "Сохранение..." : "Сохранить"}
          </button>
        </div>
      </div>

      {state.error && <p className="error-copy">{state.error}</p>}
      {hasDuplicateSlots && <p className="error-copy">У включенных QR не должен повторяться слот.</p>}
      {hasEmptyFields && <p className="error-copy">Заполните подпись для каждой строки.</p>}
      {hasMissingTigerAccounts && (
        <p className="error-copy">Укажите счёт Tiger для каждого включённого QR.</p>
      )}

      <div className="settings-table-wrap">
        <table className="settings-table">
          <thead>
            <tr>
              <th>Печатать</th>
              <th>Слот</th>
              <th>Код</th>
              <th>Подпись</th>
              <th>Provider</th>
              <th>Счёт Tiger</th>
            </tr>
          </thead>
          <tbody>
            {draft.map((item, index) => (
              <tr key={`${item.code}-${index}`}>
                <td>
                  <input
                    checked={item.enabled}
                    type="checkbox"
                    onChange={(event) => updateItem(index, { enabled: event.target.checked })}
                  />
                </td>
                <td>
                  <select
                    value={item.slot}
                    onChange={(event) => updateItem(index, { slot: Number(event.target.value) || 1 })}
                  >
                    <option value={1}>Слот 1</option>
                    <option value={2}>Слот 2</option>
                    <option value={3}>Слот 3</option>
                    <option value={4}>Слот 4</option>
                  </select>
                </td>
                <td>
                  <span className="mono">{item.code}</span>
                </td>
                <td>
                  <input
                    value={item.label}
                    onChange={(event) => updateItem(index, { label: event.target.value })}
                  />
                </td>
                <td>
                  <select
                    value={item.provider}
                    onChange={(event) =>
                      updateItem(index, { provider: event.target.value as PaymentProvider })
                    }
                  >
                    <option value="mkassa">MKassa / MBank</option>
                    <option value="odengi">O!Dengi / O!Bank</option>
                  </select>
                </td>
                <td>
                  <input
                    className="mono"
                    placeholder="BANKACC.CODE"
                    value={item.tiger_bank_account_code ?? ""}
                    onChange={(event) =>
                      updateItem(index, { tiger_bank_account_code: event.target.value || null })
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="settings-note">
        <strong>Для 1С:</strong> после этой правки расширение должно вызывать один endpoint{" "}
        <span className="mono">POST /api/v1/invoice/qr-codes</span>, а не два раза{" "}
        <span className="mono">/api/v1/qr/dynamic</span> с разными ключами.
      </div>
    </section>
  );
}

function QrDemoPanel({
  result,
  state,
  onCreate,
}: {
  result: DynamicQrResponse | null;
  state: LoadState;
  onCreate: (payload: {
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
  }) => void;
}) {
  const [provider, setProvider] = useState<PaymentProvider>("mkassa");
  const [amount, setAmount] = useState(100);
  const [branch, setBranch] = useState("");
  const [cashier, setCashier] = useState("");
  const [invoiceNumber, setInvoiceNumber] = useState("TIGER-FACTURE-1001");
  const [source, setSource] = useState("tiger");
  const [payerCode, setPayerCode] = useState("");
  const [payerFullName, setPayerFullName] = useState("");
  const [metadataKey1, setMetadataKey1] = useState("");
  const [metadataValue1, setMetadataValue1] = useState("");
  const [longLiving, setLongLiving] = useState(false);
  const usesMkassaBranchFields = provider === "mkassa";

  useEffect(() => {
    if (provider === "odengi" && longLiving) setLongLiving(false);
  }, [longLiving, provider]);

  const metadataCount = [
    invoiceNumber.trim(),
    source.trim(),
    payerCode.trim(),
    payerFullName.trim(),
    metadataKey1.trim() && metadataValue1.trim(),
  ].filter(Boolean).length;

  return (
    <section className="qr-demo-grid">
      <form
        className="form-panel"
        onSubmit={(event) => {
          event.preventDefault();
          const extraMetadata: Record<string, string> = {};
          if (metadataKey1.trim() && metadataValue1.trim()) {
            extraMetadata[metadataKey1.trim()] = metadataValue1.trim();
          }
          onCreate({
            provider,
            amount,
            branch: usesMkassaBranchFields && branch.trim() ? Number(branch) : undefined,
            cashier: usesMkassaBranchFields && cashier.trim() ? Number(cashier) : undefined,
            invoice_number: invoiceNumber.trim() || undefined,
            source: source.trim() || undefined,
            payer_code: payerCode.trim() || undefined,
            payer_full_name: payerFullName.trim() || undefined,
            metadata: Object.keys(extraMetadata).length > 0 ? extraMetadata : undefined,
            is_long_living: longLiving || undefined,
          });
        }}
      >
        <div className="provider-toggle" role="radiogroup" aria-label="Провайдер">
          <button
            className={provider === "mkassa" ? "active" : ""}
            type="button"
            onClick={() => setProvider("mkassa")}
          >
            MKassa / Мбанк
          </button>
          <button
            className={provider === "odengi" ? "active" : ""}
            type="button"
            onClick={() => setProvider("odengi")}
          >
            О!Деньги / О!Банк
          </button>
        </div>
        <p className="hint-copy">
          {provider === "odengi"
            ? "О!Деньги вернет готовые ссылки qr/link_app/site_pay; branch и cashier не нужны."
            : "MKassa принимает branch/cashier при необходимости и возвращает payment_token."}
        </p>
        <label>
          Сумма в тыйынах
          <input
            min={1}
            type="number"
            value={amount}
            onChange={(event) => setAmount(Number(event.target.value) || 1)}
          />
        </label>
        <div className="form-row">
          <label>
            Branch
            <input
              disabled={!usesMkassaBranchFields}
              inputMode="numeric"
              placeholder={usesMkassaBranchFields ? "если нужно" : "не используется"}
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
            />
          </label>
          <label>
            Cashier
            <input
              disabled={!usesMkassaBranchFields}
              inputMode="numeric"
              placeholder={usesMkassaBranchFields ? "если нужно" : "не используется"}
              value={cashier}
              onChange={(event) => setCashier(event.target.value)}
            />
          </label>
        </div>
        <label>
          Код фактуры Tiger
          <input value={invoiceNumber} onChange={(event) => setInvoiceNumber(event.target.value)} />
        </label>
        <label>
          Источник
          <input value={source} onChange={(event) => setSource(event.target.value)} />
        </label>
        <label>
          ИНН / код плательщика
          <input value={payerCode} onChange={(event) => setPayerCode(event.target.value)} />
        </label>
        <label>
          Наименование плательщика
          <input value={payerFullName} onChange={(event) => setPayerFullName(event.target.value)} />
        </label>
        <div className="form-row">
          <label>
            Metadata key
            <input value={metadataKey1} onChange={(event) => setMetadataKey1(event.target.value)} />
          </label>
          <label>
            Metadata value
            <input value={metadataValue1} onChange={(event) => setMetadataValue1(event.target.value)} />
          </label>
        </div>
        <p className={metadataCount > 5 ? "error-copy" : "hint-copy"}>
          Metadata: {metadataCount}/5
        </p>
        {provider === "mkassa" ? (
          <label className="check-row">
            <input
              checked={longLiving}
              type="checkbox"
              onChange={(event) => setLongLiving(event.target.checked)}
            />
            Long living QR
          </label>
        ) : (
          <p className="hint-copy">О!Деньги создается как одноразовый QR на 24 часа.</p>
        )}
        <button className="refresh" disabled={state.loading} type="submit">
          <Icon name="qr" size={16} />
          {state.loading ? "Создание..." : "Создать QR"}
        </button>
        {state.error && <p className="error-copy">{state.error}</p>}
      </form>

      <aside className="details-panel qr-result-panel">
        <div className="details-title">
          <Icon name="qr" size={18} />
          <h2>Результат</h2>
        </div>
        {!result ? (
          <p className="empty-copy">Заполните поля и создайте динамический QR.</p>
        ) : (
          <>
            <img
              alt="QR code"
              className="qr-image"
              height={260}
              src={qrImageUrl(result.payment_token)}
              width={260}
            />
            <dl>
              <dt>ID</dt>
              <dd className="mono">{result.id}</dd>
              {result.invoice_id && (
                <>
                  <dt>ID счета в банке</dt>
                  <dd className="mono">{result.invoice_id}</dd>
                </>
              )}
              <dt>Status</dt>
              <dd><span className={`status ${statusTone(result.status)}`}>{result.status}</span></dd>
              <dt>Amount</dt>
              <dd>{formatAmount(result.amount)}</dd>
            </dl>
            <a className="qr-link" href={result.payment_token} rel="noreferrer" target="_blank">
              Открыть ссылку QR
            </a>
            <ProviderLinks result={result} />
            <h3>Raw payload</h3>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </>
        )}
      </aside>
    </section>
  );
}

function ProviderLinks({ result }: { result: DynamicQrResponse }) {
  const links = [
    { key: "qr_url", label: "QR URL", value: result.qr_url },
    { key: "link_app", label: "App link", value: result.link_app },
    { key: "site_pay", label: "Site pay", value: result.site_pay },
    { key: "qr", label: "QR image", value: result.qr },
  ].filter(
    (item, index, items) =>
      item.value && items.findIndex((candidate) => candidate.value === item.value) === index,
  );

  if (links.length === 0) return null;
  return (
    <div className="provider-links">
      {links.map((link) => (
        <a className="qr-link" href={link.value || "#"} key={link.key} rel="noreferrer" target="_blank">
          {link.label}
        </a>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <Icon name="database" size={34} />
      <h2>Пока нет данных</h2>
      <p>Создайте QR или дождитесь callback, после этого записи появятся здесь.</p>
    </div>
  );
}

export default App;

function LoginPage({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await loginViaIdentity(email, password);
      onLoggedIn();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось войти. Проверьте логин и пароль.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="form-panel login-panel" onSubmit={submit}>
        <div>
          <h1>Вход в Turkuaz Payments</h1>
          <p className="hint-copy">Вход через единый модуль пользователей.</p>
        </div>
        <label>
          Email Identity
          <input
            autoComplete="username"
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label>
          Пароль
          <input
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && <p className="error-copy">{error}</p>}
        <button className="refresh" disabled={submitting} type="submit">
          {submitting ? "Вход..." : "Войти"}
        </button>
      </form>
    </main>
  );
}

function backendUrl(port: number, path = ""): string {
  if (typeof window === "undefined") return `http://localhost:${port}${path}`;
  return `${window.location.protocol}//${window.location.hostname}:${port}${path}`;
}

function getStoredIdentityToken(): string | null {
  return getToken();
}

async function fetchCurrentIdentityUser(options: {
  identityApiBaseUrl: string;
  tokenStorageKeys: string[];
}): Promise<CurrentIdentityUser> {
  const token = readStoredToken(options.tokenStorageKeys);
  const headers = new Headers({ Accept: "application/json" });
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${options.identityApiBaseUrl}/auth/me`, { headers });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || `HTTP ${response.status}`);
  }
  return data as CurrentIdentityUser;
}

function readStoredToken(keys: string[]): string | null {
  if (typeof window === "undefined") return null;
  for (const key of keys) {
    const token = window.localStorage.getItem(key);
    if (token) return token;
  }
  return null;
}

function handleLogout(): void {
  clearToken();
  window.location.href = "/login";
}

function userMenuFromIdentityUser(user: CurrentIdentityUser, onLogout: () => void): UserConfig {
  return {
    name: user.full_name || user.email,
    email: user.email,
    role: user.roles[0] || user.branch_name || "Payments",
    actions: [{ key: "logout", label: "Выйти", icon: "logout", onClick: onLogout }],
  };
}

function userMenuFromClaims(claims: Record<string, unknown>, onLogout: () => void): UserConfig | undefined {
  const email = stringClaim(claims.email);
  const name = stringClaim(claims.full_name) || email;
  if (!name) return undefined;
  return {
    name,
    email,
    role: firstStringClaim(claims.roles) || stringClaim(claims.branch_name) || "Payments",
    actions: [{ key: "logout", label: "Выйти", icon: "logout", onClick: onLogout }],
  };
}

function firstStringClaim(value: unknown): string | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.find((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function stringClaim(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}
