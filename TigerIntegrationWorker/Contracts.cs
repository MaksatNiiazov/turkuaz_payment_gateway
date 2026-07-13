using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

public sealed class FlexibleDateTimeOffsetConverter : JsonConverter<DateTimeOffset>
{
    private static readonly TimeSpan BishkekOffset = TimeSpan.FromHours(6);

    public override DateTimeOffset Read(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.String)
        {
            var text = reader.GetString();
            if (!string.IsNullOrWhiteSpace(text) &&
                DateTimeOffset.TryParse(
                    text,
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AllowWhiteSpaces,
                    out var withOffset))
            {
                return withOffset;
            }

            if (!string.IsNullOrWhiteSpace(text) &&
                DateTime.TryParse(
                    text,
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AllowWhiteSpaces,
                    out var localTime))
            {
                return new DateTimeOffset(
                    DateTime.SpecifyKind(localTime, DateTimeKind.Unspecified),
                    BishkekOffset);
            }
        }

        if (reader.TokenType == JsonTokenType.Number && reader.TryGetInt64(out var timestamp))
        {
            if (timestamp >= 100_000_000_000)
                timestamp /= 1000;
            try
            {
                return DateTimeOffset.FromUnixTimeSeconds(timestamp);
            }
            catch (ArgumentOutOfRangeException)
            {
                // Fall through to the default value so one malformed queue item
                // can be reported without stopping the whole polling cycle.
            }
        }

        return default;
    }

    public override void Write(
        Utf8JsonWriter writer,
        DateTimeOffset value,
        JsonSerializerOptions options) => writer.WriteStringValue(value.ToString("O"));
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

public sealed record VoucherInspectResult(
    bool Success,
    string FicheNo,
    IReadOnlyList<VoucherDebugSnapshot> Vouchers,
    string? Error);

public sealed record InvoicePaidEvent(
    string InvoiceId,
    string? InvoiceNumber,
    string PaidTransactionId,
    string PaidProvider,
    string? ProviderPaymentId,
    string? TargetBankCode,
    string? TargetBankAccountCode,
    [property: JsonConverter(typeof(FlexibleDateTimeOffsetConverter))]
    DateTimeOffset PaidAt,
    long AmountTyiyn,
    decimal Amount,
    string Currency,
    string? ClientCode,
    string? ClientName,
    string? PaymentMethod,
    string? Description);

public sealed record InvoiceProcessResult(
    bool Success,
    string InvoiceId,
    bool DryRun,
    bool AlreadyExists,
    int? TigerLogicalRef,
    string? TigerFicheNo,
    int? SavedLineCount,
    int? PaymentListCount,
    string Marker,
    string? Error);

public sealed class TigerQueueItem
{
    [JsonPropertyName("id")]
    public long Id { get; init; }

    [JsonPropertyName("attempt_count")]
    public int AttemptCount { get; init; }

    [JsonPropertyName("status")]
    public string? Status { get; init; }

    [JsonPropertyName("event_payload")]
    public InvoicePaidEvent? EventPayload { get; init; }
}

public sealed record TigerQueueResult(
    [property: JsonPropertyName("success")] bool Success,
    [property: JsonPropertyName("tiger_logical_ref")] string? TigerLogicalRef,
    [property: JsonPropertyName("tiger_fiche_no")] string? TigerFicheNo,
    [property: JsonPropertyName("error_message")] string? ErrorMessage);

public sealed record AppendStrategyDebugRequest(
    string? TargetBankAccountCode,
    string? ClientCode,
    string? DocumentDate,
    decimal? Amount,
    int? AppendCount);

public sealed record AppendStrategyDebugResult(
    bool Success,
    string Strategy,
    string GroupMarker,
    string FirstLineMarker,
    string SecondLineMarker,
    IReadOnlyList<string> ExpectedLineMarkers,
    int? BaseVoucherRef,
    string? BaseFicheNo,
    IReadOnlyList<VoucherDebugSnapshot> Snapshots,
    string? Error);

public sealed record VoucherDebugSnapshot(
    int LogicalRef,
    string FicheNo,
    decimal HeaderDebit,
    string GroupMarker,
    int LineCount,
    decimal LineAmountSum,
    IReadOnlyList<string> LineMarkers);
