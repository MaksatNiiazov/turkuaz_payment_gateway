import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell, fetchServiceRegistry, Icon, serviceLinksFromRegistry } from "@turkuaz/ui";
import type { ServiceRegistryItem } from "@turkuaz/ui";
import {
  cancelTransaction,
  createDemoDynamicQr,
  fetchAccessEvents,
  fetchTransactions,
  fetchWebhooks,
  qrImageUrl,
  refreshTransaction,
} from "./api";
import type { AccessEvent, DynamicQrResponse, TransactionRow, ViewMode, WebhookEvent } from "./types";

const IDENTITY_API_BASE_URL = import.meta.env.VITE_IDENTITY_API_BASE_URL || "/identity-api";

type LoadState = {
  loading: boolean;
  error: string | null;
};

function statusTone(status?: string | null): string {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return "good";
    case "failed":
    case "canceled":
    case "overdue":
      return "bad";
    case "inited":
    case "waiting":
    case "qr_scanned":
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

function App() {
  const [view, setView] = useState<ViewMode>("transactions");
  const [limit, setLimit] = useState(50);
  const [statusFilter, setStatusFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [transactions, setTransactions] = useState<TransactionRow[]>([]);
  const [webhooks, setWebhooks] = useState<WebhookEvent[]>([]);
  const [accessEvents, setAccessEvents] = useState<AccessEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [state, setState] = useState<LoadState>({ loading: false, error: null });
  const [cancelingId, setCancelingId] = useState<string | null>(null);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [qrResult, setQrResult] = useState<DynamicQrResponse | null>(null);
  const [qrState, setQrState] = useState<LoadState>({ loading: false, error: null });
  const [registeredServices, setRegisteredServices] = useState<ServiceRegistryItem[]>([]);

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

  useEffect(() => {
    if (view !== "qr-demo") void loadData();
  }, [loadData, view]);

  useEffect(() => {
    let cancelled = false;
    void fetchServiceRegistry({ identityApiBaseUrl: IDENTITY_API_BASE_URL })
      .then((services) => {
        if (!cancelled) setRegisteredServices(services);
      })
      .catch(() => {
        if (!cancelled) setRegisteredServices([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    amount: number;
    invoice_number?: string;
    source?: string;
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

  const navItems = [
    {
      key: "transactions",
      label: "Транзакции",
      icon: "banknote" as const,
      permissions: ["payments.transactions.read"],
      active: view === "transactions",
      onClick: () => setView("transactions"),
    },
    {
      key: "webhooks",
      label: "Webhooks",
      icon: "webhook" as const,
      permissions: ["payments.transactions.read"],
      active: view === "webhooks",
      onClick: () => setView("webhooks"),
    },
    {
      key: "access",
      label: "Доступы",
      icon: "database" as const,
      permissions: ["payments.transactions.read"],
      active: view === "access",
      onClick: () => setView("access"),
    },
    {
      key: "qr-demo",
      label: "QR Demo",
      icon: "qr" as const,
      permissions: ["payments.qr.create"],
      active: view === "qr-demo",
      onClick: () => setView("qr-demo"),
    },
  ];
  const pageTitle =
    view === "transactions"
      ? "Транзакции"
      : view === "webhooks"
        ? "Webhook события"
        : view === "access"
          ? "Доступы"
          : "QR Demo";
  const pageDescription =
    view === "qr-demo"
      ? "Создание тестового динамического QR через backend API."
      : "Операционная панель для просмотра платежей, callback'ов и обращений интеграций.";

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
        { href: "/docs", label: "Swagger", icon: "file", permissions: ["payments.transactions.read", "payments.qr.create"] },
      ]}
      serviceName="Payments"
      pageTitle={pageTitle}
      pageDescription={pageDescription}
      breadcrumbs={[{ label: "Payments" }, { label: pageTitle }]}
      headerActions={[
        { key: "refresh", label: "Обновить", icon: "refresh", onClick: () => void loadData() },
      ]}
      environment="local"
      version="v0.1.0"
      apiStatus={state.error || qrState.error ? "degraded" : "online"}
      footerLinks={[{ href: "/docs", label: "Swagger" }]}
    >
        {view === "qr-demo" ? (
          <QrDemoPanel
            result={qrResult}
            state={qrState}
            onCreate={(payload) => void handleCreateDemoQr(payload)}
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
  return (
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Provider</th>
          <th>Status</th>
          <th>Type</th>
          <th>Amount</th>
          <th>Invoice</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            className={row.id === selectedId ? "selected" : ""}
            key={row.id}
            onClick={() => onSelect(row.id)}
          >
            <td className="mono">{truncate(row.id, 24)}</td>
            <td>{row.provider}</td>
            <td><span className={`status ${statusTone(row.status)}`}>{row.status || "unknown"}</span></td>
            <td>{row.transaction_type || "-"}</td>
            <td>{formatAmount(row.amount)}</td>
            <td>{row.metadata?.invoice_number || row.metadata?.order_id || "-"}</td>
            <td>{formatDate(row.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
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

function QrDemoPanel({
  result,
  state,
  onCreate,
}: {
  result: DynamicQrResponse | null;
  state: LoadState;
  onCreate: (payload: {
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
            amount,
            branch: branch.trim() ? Number(branch) : undefined,
            cashier: cashier.trim() ? Number(cashier) : undefined,
            invoice_number: invoiceNumber.trim() || undefined,
            source: source.trim() || undefined,
            payer_code: payerCode.trim() || undefined,
            payer_full_name: payerFullName.trim() || undefined,
            metadata: Object.keys(extraMetadata).length > 0 ? extraMetadata : undefined,
            is_long_living: longLiving || undefined,
          });
        }}
      >
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
              inputMode="numeric"
              placeholder="если нужно"
              value={branch}
              onChange={(event) => setBranch(event.target.value)}
            />
          </label>
          <label>
            Cashier
            <input
              inputMode="numeric"
              placeholder="если нужно"
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
        <label className="check-row">
          <input
            checked={longLiving}
            type="checkbox"
            onChange={(event) => setLongLiving(event.target.checked)}
          />
          Long living QR
        </label>
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
              <dt>Status</dt>
              <dd><span className={`status ${statusTone(result.status)}`}>{result.status}</span></dd>
              <dt>Amount</dt>
              <dd>{formatAmount(result.amount)}</dd>
            </dl>
            <a className="qr-link" href={result.payment_token} rel="noreferrer" target="_blank">
              Открыть ссылку QR
            </a>
            <h3>Raw payload</h3>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </>
        )}
      </aside>
    </section>
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
