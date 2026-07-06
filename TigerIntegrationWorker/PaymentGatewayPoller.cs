using System.Net.Http.Json;
using Microsoft.Extensions.Options;

public sealed class PaymentGatewayClient
{
    private readonly HttpClient _http;
    private readonly GatewayOptions _options;

    public PaymentGatewayClient(HttpClient http, IOptions<GatewayOptions> options)
    {
        _http = http;
        _options = options.Value;
    }

    public async Task<IReadOnlyList<TigerQueueItem>> GetPendingAsync(CancellationToken cancellationToken)
    {
        using var request = CreateRequest(
            HttpMethod.Get,
            $"/api/v1/local/tiger/invoice-events/pending?limit={Math.Clamp(_options.BatchSize, 1, 100)}");
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<List<TigerQueueItem>>(cancellationToken)
            ?? [];
    }

    public async Task ReportAsync(long eventId, TigerQueueResult result, CancellationToken cancellationToken)
    {
        using var request = CreateRequest(
            HttpMethod.Post,
            $"/api/v1/local/tiger/invoice-events/{eventId}/result");
        request.Content = JsonContent.Create(result);
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    private HttpRequestMessage CreateRequest(HttpMethod method, string path)
    {
        if (!Uri.TryCreate(_options.BaseUrl, UriKind.Absolute, out var baseUri))
            throw new InvalidOperationException("Gateway:BaseUrl must be an absolute URL.");
        if (string.IsNullOrWhiteSpace(_options.IntegrationKey))
            throw new InvalidOperationException("Gateway:IntegrationKey is not configured.");

        var request = new HttpRequestMessage(method, new Uri(baseUri, path));
        request.Headers.Add("X-Integration-Key", _options.IntegrationKey);
        return request;
    }
}

public sealed class PaymentGatewayPoller : BackgroundService
{
    private readonly GatewayOptions _options;
    private readonly PaymentGatewayClient _gateway;
    private readonly LogoObjectsClient _logo;
    private readonly ILogger<PaymentGatewayPoller> _logger;

    public PaymentGatewayPoller(
        IOptions<GatewayOptions> options,
        PaymentGatewayClient gateway,
        LogoObjectsClient logo,
        ILogger<PaymentGatewayPoller> logger)
    {
        _options = options.Value;
        _gateway = gateway;
        _logo = logo;
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_options.Enabled)
        {
            _logger.LogInformation("PaymentGateway polling is disabled.");
            return;
        }

        var interval = TimeSpan.FromMinutes(Math.Max(1, _options.PollIntervalMinutes));
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var events = await _gateway.GetPendingAsync(stoppingToken);
                foreach (var item in events)
                {
                    if (item.EventPayload is null ||
                        !string.Equals(item.Status, "pending", StringComparison.OrdinalIgnoreCase))
                        continue;

                    var result = _logo.ProcessInvoicePaid(item.EventPayload);
                    if (result.DryRun)
                    {
                        _logger.LogInformation(
                            "Dry-run validated Tiger event {EventId} for invoice {InvoiceId}; result was not acknowledged.",
                            item.Id,
                            result.InvoiceId);
                        continue;
                    }

                    await _gateway.ReportAsync(
                        item.Id,
                        new TigerQueueResult(
                            result.Success,
                            result.TigerLogicalRef?.ToString(),
                            result.TigerFicheNo,
                            result.Error),
                        stoppingToken);
                }
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Tiger polling cycle failed.");
            }

            await Task.Delay(interval, stoppingToken);
        }
    }
}
