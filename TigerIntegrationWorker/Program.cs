using System.Runtime.InteropServices;
using System.Security.Cryptography;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<TigerOptions>(builder.Configuration.GetSection("Tiger"));
builder.Services.AddSingleton<LogoObjectsClient>();

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

app.MapPost("/api/payments", (PaymentPaidEvent payment, LogoObjectsClient logo) =>
{
    var result = logo.AcceptPaymentDryRun(payment);
    return result.Success ? Results.Accepted(null, result) : Results.Problem(result.Error);
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

public sealed class LogoObjectsClient
{
    private readonly TigerOptions _options;

    public LogoObjectsClient(IConfiguration configuration)
    {
        _options = configuration.GetSection("Tiger").Get<TigerOptions>() ?? new TigerOptions();
    }

    public TigerVersionResult GetVersion()
    {
        dynamic? logo = null;

        try
        {
            logo = CreateUnityApplication();
            return new TigerVersionResult(true, (string)logo.Version(), (string)logo.GetAppPath(), null);
        }
        catch (Exception ex)
        {
            return new TigerVersionResult(false, null, null, ex.Message);
        }
        finally
        {
            Release(logo);
        }
    }

    public TigerLoginResult TestLogin()
    {
        dynamic? logo = null;

        try
        {
            logo = CreateUnityApplication();
            ConnectAndLogin(logo);

            return new TigerLoginResult(
                true,
                (bool)logo.Connected,
                (bool)logo.LoggedIn,
                (bool)logo.CompanyLoggedIn,
                (int)logo.CurrentFirm,
                (int)logo.CurrentPeriod,
                (int)logo.GetLastError(),
                Convert.ToString(logo.GetLastErrorString()),
                null);
        }
        catch (Exception ex)
        {
            return new TigerLoginResult(false, false, false, false, -1, -1, null, null, ex.Message);
        }
        finally
        {
            LogoutAndRelease(logo);
        }
    }

    public TigerClientsResult GetSampleClients(int count)
    {
        dynamic? logo = null;
        dynamic? query = null;

        try
        {
            logo = CreateUnityApplication();
            ConnectAndLogin(logo);

            query = logo.NewQuery();
            query.Statement = $"SELECT TOP {count} LOGICALREF, CODE, DEFINITION_ FROM LG_{_options.FirmNo}_CLCARD ORDER BY LOGICALREF";

            var ok = (bool)query.OpenDirect();
            if (!ok)
            {
                return new TigerClientsResult(false, [], "Query OpenDirect returned false.");
            }

            var clients = new List<TigerClientRow>();
            var hasRow = (bool)query.First();
            if (!hasRow)
            {
                return new TigerClientsResult(true, clients, null);
            }

            for (var index = 0; index < count; index++)
            {
                clients.Add(new TigerClientRow(
                    Convert.ToInt32(query.FieldByName("LOGICALREF").Value),
                    Convert.ToString(query.FieldByName("CODE").Value) ?? string.Empty,
                    Convert.ToString(query.FieldByName("DEFINITION_").Value) ?? string.Empty));

                var hasNext = (bool)query.Next();
                if (!hasNext)
                {
                    break;
                }
            }

            return new TigerClientsResult(true, clients, null);
        }
        catch (Exception ex)
        {
            return new TigerClientsResult(false, [], ex.Message);
        }
        finally
        {
            try { query?.Close(); } catch { }
            LogoutAndRelease(logo);
        }
    }

    public PaymentAcceptResult AcceptPaymentDryRun(PaymentPaidEvent payment)
    {
        if (!_options.DryRun)
        {
            return new PaymentAcceptResult(false, payment.ExternalPaymentId, true, "Write mode is not implemented yet.");
        }

        if (string.IsNullOrWhiteSpace(payment.ExternalPaymentId))
        {
            return new PaymentAcceptResult(false, payment.ExternalPaymentId, true, "externalPaymentId is required.");
        }

        if (payment.Amount <= 0)
        {
            return new PaymentAcceptResult(false, payment.ExternalPaymentId, true, "amount must be greater than zero.");
        }

        return new PaymentAcceptResult(true, payment.ExternalPaymentId, true, null);
    }

    private dynamic CreateUnityApplication()
    {
        var type = Type.GetTypeFromProgID("UnityObjects.UnityApplication")
            ?? throw new InvalidOperationException("UnityObjects.UnityApplication is not registered.");

        return Activator.CreateInstance(type)
            ?? throw new InvalidOperationException("Could not create UnityObjects.UnityApplication.");
    }

    private void ConnectAndLogin(dynamic logo)
    {
        if (string.IsNullOrWhiteSpace(_options.UserName))
        {
            throw new InvalidOperationException("Tiger:UserName is not configured.");
        }

        if (string.IsNullOrWhiteSpace(_options.Password))
        {
            throw new InvalidOperationException("Tiger:Password is not configured.");
        }

        if (!(bool)logo.Connect())
        {
            throw new InvalidOperationException("Logo Connect returned false.");
        }

        if (!(bool)logo.UserLogin(_options.UserName, _options.Password))
        {
            throw new InvalidOperationException("Logo UserLogin returned false.");
        }

        if (!(bool)logo.CompanyLogin(_options.FirmNo))
        {
            throw new InvalidOperationException($"Logo CompanyLogin({_options.FirmNo}) returned false.");
        }

        if ((int)logo.CurrentPeriod != _options.PeriodNo)
        {
            throw new InvalidOperationException(
                $"Unexpected Logo period. Expected {_options.PeriodNo}, got {(int)logo.CurrentPeriod}.");
        }
    }

    private static void LogoutAndRelease(dynamic? logo)
    {
        if (logo is null)
        {
            return;
        }

        try { logo.CompanyLogout(); } catch { }
        try { logo.UserLogout(); } catch { }
        try { logo.Disconnect(); } catch { }
        Release(logo);
    }

    private static void Release(dynamic? comObject)
    {
        if (comObject is null)
        {
            return;
        }

        try
        {
            Marshal.ReleaseComObject(comObject);
        }
        catch
        {
            // Best-effort cleanup for COM smoke tests.
        }
    }
}

public sealed class TigerOptions
{
    public string UserName { get; set; } = string.Empty;
    public string Password { get; set; } = string.Empty;
    public int FirmNo { get; set; } = 126;
    public int PeriodNo { get; set; } = 1;
    public string IntegrationKey { get; set; } = string.Empty;
    public bool DryRun { get; set; } = true;
}

public sealed record TigerVersionResult(bool Success, string? Version, string? AppPath, string? Error);

public sealed record TigerLoginResult(
    bool Success,
    bool Connected,
    bool LoggedIn,
    bool CompanyLoggedIn,
    int CurrentFirm,
    int CurrentPeriod,
    int? LastError,
    string? LastErrorString,
    string? Error);

public sealed record TigerClientsResult(bool Success, IReadOnlyList<TigerClientRow> Clients, string? Error);

public sealed record TigerClientRow(int LogicalRef, string Code, string Name);

public sealed record PaymentPaidEvent(
    string ExternalPaymentId,
    string GatewayTransactionId,
    string Provider,
    string? ProviderPaymentId,
    string InvoiceId,
    string? InvoiceNumber,
    DateTimeOffset PaidAt,
    long AmountTyiyn,
    decimal Amount,
    string Currency,
    string? ClientCode,
    string? ClientName,
    string? PaymentMethod,
    string? Description);

public sealed record PaymentAcceptResult(bool Success, string ExternalPaymentId, bool DryRun, string? Error);
