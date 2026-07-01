public sealed class TigerOptions
{
    public string UserName { get; set; } = string.Empty;
    public string Password { get; set; } = string.Empty;
    public int FirmNo { get; set; } = 126;
    public int PeriodNo { get; set; } = 1;
    public string IntegrationKey { get; set; } = string.Empty;
    public bool DryRun { get; set; } = true;
    public int[] AllowedWriteFirmNos { get; set; } = [];
    public DateOnly? TestDocumentDateOverride { get; set; }
}

public sealed class GatewayOptions
{
    public bool Enabled { get; set; }
    public string BaseUrl { get; set; } = string.Empty;
    public string IntegrationKey { get; set; } = string.Empty;
    public int PollIntervalMinutes { get; set; } = 30;
    public int BatchSize { get; set; } = 20;
    public int MaxAttempts { get; set; } = 5;
}
