using System.Security.Cryptography;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<TigerOptions>(builder.Configuration.GetSection("Tiger"));
builder.Services.Configure<GatewayOptions>(builder.Configuration.GetSection("Gateway"));
builder.Services.AddSingleton<LogoObjectsClient>();
builder.Services.AddHttpClient<PaymentGatewayClient>();
builder.Services.AddHostedService<PaymentGatewayPoller>();

var app = builder.Build();

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.MapGet("/tiger/version", (LogoObjectsClient logo) =>
{
    var result = logo.GetVersion();
    return result.Success ? Results.Ok(result) : Results.Problem(result.Error);
}).RequireIntegrationKey();

app.MapPost("/tiger/test-login", (LogoObjectsClient logo) =>
{
    var result = logo.TestLogin();
    return result.Success ? Results.Ok(result) : Results.Problem(result.Error);
}).RequireIntegrationKey();

app.MapGet("/tiger/clients/sample", (LogoObjectsClient logo) =>
{
    var result = logo.GetSampleClients(5);
    return result.Success ? Results.Ok(result) : Results.Problem(result.Error);
}).RequireIntegrationKey();

app.MapPost("/api/invoices/paid", (InvoicePaidEvent invoice, LogoObjectsClient logo) =>
{
    var result = logo.ProcessInvoicePaid(invoice);
    return result.Success ? Results.Ok(result) : Results.BadRequest(result);
}).RequireIntegrationKey();

app.Run();

public static class IntegrationKeyEndpointExtensions
{
    public static RouteHandlerBuilder RequireIntegrationKey(this RouteHandlerBuilder builder)
    {
        return builder.AddEndpointFilter(async (context, next) =>
        {
            var config = context.HttpContext.RequestServices.GetRequiredService<IConfiguration>();
            var expectedKey = config["Tiger:IntegrationKey"];

            if (string.IsNullOrWhiteSpace(expectedKey))
            {
                return Results.Problem("Tiger:IntegrationKey is not configured.", statusCode: 500);
            }

            if (!context.HttpContext.Request.Headers.TryGetValue("X-Integration-Key", out var actualKey)
                || actualKey.Count == 0
                || !FixedTimeEquals(actualKey[0] ?? string.Empty, expectedKey))
            {
                return Results.Unauthorized();
            }

            return await next(context);
        });
    }

    private static bool FixedTimeEquals(string left, string right)
    {
        var leftBytes = System.Text.Encoding.UTF8.GetBytes(left);
        var rightBytes = System.Text.Encoding.UTF8.GetBytes(right);
        return CryptographicOperations.FixedTimeEquals(leftBytes, rightBytes);
    }
}
