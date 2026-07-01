using System.Globalization;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Xml.Linq;

public sealed class LogoObjectsClient
{
    private const int BankVoucherDataObjectType = 24;
    private readonly TigerOptions _options;
    private readonly object _comLock = new();

    public LogoObjectsClient(IConfiguration configuration)
    {
        _options = configuration.GetSection("Tiger").Get<TigerOptions>() ?? new TigerOptions();
    }

    public TigerVersionResult GetVersion()
    {
        lock (_comLock)
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
    }

    public TigerLoginResult TestLogin()
    {
        lock (_comLock)
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
    }

    public TigerClientsResult GetSampleClients(int count)
    {
        lock (_comLock)
        {
            dynamic? logo = null;
            dynamic? query = null;
            try
            {
                logo = CreateUnityApplication();
                ConnectAndLogin(logo);
                query = logo.NewQuery();
                query.Statement = $"SELECT TOP {count} LOGICALREF, CODE, DEFINITION_ "
                    + $"FROM LG_{_options.FirmNo:000}_CLCARD ORDER BY LOGICALREF";

                if (!(bool)query.OpenDirect())
                {
                    return new TigerClientsResult(false, [], "Query OpenDirect returned false.");
                }

                var clients = new List<TigerClientRow>();
                var hasRow = (bool)query.First();
                while (hasRow && clients.Count < count)
                {
                    clients.Add(new TigerClientRow(
                        Convert.ToInt32(query.FieldByName("LOGICALREF").Value),
                        Convert.ToString(query.FieldByName("CODE").Value) ?? string.Empty,
                        Convert.ToString(query.FieldByName("DEFINITION_").Value) ?? string.Empty));
                    hasRow = (bool)query.Next();
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
                Release(query);
                LogoutAndRelease(logo);
            }
        }
    }

    public InvoiceProcessResult ProcessInvoicePaid(InvoicePaidEvent invoice)
    {
        var validationError = ValidateInvoice(invoice);
        if (validationError is not null)
        {
            return Failure(invoice.InvoiceId ?? string.Empty, "PG:INVALID", validationError);
        }
        var marker = BuildMarker(invoice.InvoiceId);

        lock (_comLock)
        {
            dynamic? logo = null;
            dynamic? data = null;
            dynamic? readData = null;
            try
            {
                logo = CreateUnityApplication();
                ConnectAndLogin(logo);

                if (!_options.DryRun)
                {
                    EnsureWriteIsAllowed();
                    var existing = FindExistingVoucher(logo, marker);
                    if (existing is not null)
                    {
                        return new InvoiceProcessResult(
                            true,
                            invoice.InvoiceId,
                            false,
                            true,
                            existing.Value.LogicalRef,
                            existing.Value.FicheNo,
                            null,
                            null,
                            marker,
                            null);
                    }
                }

                var xml = BuildBankVoucherXml(invoice, marker, ResolveDocumentDate(invoice));
                data = logo.NewDataObject(BankVoucherDataObjectType);
                if (!(bool)data.ImportFromXmlStr("BANK_VOUCHERS", xml))
                {
                    return Failure(invoice.InvoiceId, marker, BuildDataError(data, "XML import failed"));
                }

                var importedLineCount = Convert.ToInt32(
                    data.DataFields.FieldByName("TRANSACTIONS").Lines.Count);
                if (importedLineCount != 1)
                {
                    return Failure(invoice.InvoiceId, marker, $"Expected one imported line, got {importedLineCount}.");
                }

                if (_options.DryRun)
                {
                    return new InvoiceProcessResult(
                        true,
                        invoice.InvoiceId,
                        true,
                        false,
                        null,
                        null,
                        importedLineCount,
                        null,
                        marker,
                        null);
                }

                if (!(bool)data.Post())
                {
                    return Failure(invoice.InvoiceId, marker, BuildDataError(data, "Tiger Post returned false"));
                }

                var logicalRef = Convert.ToInt32(
                    data.DataFields.FieldByName("INTERNAL_REFERENCE").Value,
                    CultureInfo.InvariantCulture);
                var ficheNo = Convert.ToString(
                    data.DataFields.FieldByName("NUMBER").Value,
                    CultureInfo.InvariantCulture);

                readData = logo.NewDataObject(BankVoucherDataObjectType);
                if (!(bool)readData.Read(logicalRef))
                {
                    throw new InvalidOperationException(
                        $"Voucher {logicalRef} was posted but could not be read back.");
                }

                dynamic savedLines = readData.DataFields.FieldByName("TRANSACTIONS").Lines;
                var savedLineCount = Convert.ToInt32(savedLines.Count);
                if (savedLineCount != 1)
                {
                    throw new InvalidOperationException(
                        $"Voucher {logicalRef} was posted with {savedLineCount} lines; expected one.");
                }

                dynamic savedLine = savedLines.Item(0);
                var paymentListCount = Convert.ToInt32(
                    savedLine.FieldByName("PAYMENT_LIST").Lines.Count);

                return new InvoiceProcessResult(
                    true,
                    invoice.InvoiceId,
                    false,
                    false,
                    logicalRef,
                    ficheNo,
                    savedLineCount,
                    paymentListCount,
                    marker,
                    null);
            }
            catch (Exception ex)
            {
                return Failure(invoice.InvoiceId, marker, ex.Message);
            }
            finally
            {
                Release(readData);
                Release(data);
                LogoutAndRelease(logo);
            }
        }
    }

    private string? ValidateInvoice(InvoicePaidEvent invoice)
    {
        if (string.IsNullOrWhiteSpace(invoice.InvoiceId)) return "invoiceId is required.";
        if (string.IsNullOrWhiteSpace(invoice.PaidTransactionId)) return "paidTransactionId is required.";
        if (invoice.PaidAt == default) return "paidAt is required.";
        if (invoice.Amount <= 0) return "amount must be greater than zero.";
        if (!string.Equals(invoice.Currency, "KGS", StringComparison.OrdinalIgnoreCase))
            return "Only KGS bank vouchers are currently supported.";
        if (string.IsNullOrWhiteSpace(invoice.ClientCode)) return "clientCode is required.";
        if (string.IsNullOrWhiteSpace(invoice.TargetBankAccountCode))
            return "targetBankAccountCode must contain the Tiger BANKACC.CODE.";
        return null;
    }

    private void EnsureWriteIsAllowed()
    {
        if (!_options.AllowedWriteFirmNos.Contains(_options.FirmNo))
        {
            throw new InvalidOperationException(
                $"Writes to firm {_options.FirmNo} are not explicitly allowed by Tiger:AllowedWriteFirmNos.");
        }
    }

    private DateOnly ResolveDocumentDate(InvoicePaidEvent invoice)
    {
        if (_options.TestDocumentDateOverride is not null)
        {
            if (_options.FirmNo != 923)
            {
                throw new InvalidOperationException(
                    "Tiger:TestDocumentDateOverride can only be used with test firm 923.");
            }
            return _options.TestDocumentDateOverride.Value;
        }
        return DateOnly.FromDateTime(invoice.PaidAt.LocalDateTime);
    }

    private static string BuildBankVoucherXml(InvoicePaidEvent invoice, string marker, DateOnly date)
    {
        var dateText = date.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture);
        var amount = invoice.Amount.ToString("0.##", CultureInfo.InvariantCulture);
        var document = new XDocument(
            new XElement("BANK_VOUCHERS",
                new XElement("BANK_VOUCHER",
                    new XAttribute("DBOP", "INS"),
                    new XElement("DATE", dateText),
                    new XElement("TYPE", "3"),
                    new XElement("TOTAL_DEBIT", amount),
                    new XElement("NOTES1", marker),
                    new XElement("CURRSEL_TOTALS", "1"),
                    new XElement("DIVISION", "0"),
                    new XElement("DEPARMENT", "0"),
                    new XElement("TRANSACTIONS",
                        new XElement("TRANSACTION",
                            new XElement("TYPE", "1"),
                            new XElement("BANKACC_CODE", invoice.TargetBankAccountCode!.Trim()),
                            new XElement("ARP_CODE", invoice.ClientCode!.Trim()),
                            new XElement("DATE", dateText),
                            new XElement("TRCODE", "3"),
                            new XElement("MODULENR", "7"),
                            new XElement("CURR_TRANS", "37"),
                            new XElement("DEBIT", amount),
                            new XElement("AMOUNT", amount),
                            new XElement("TC_XRATE", "1"),
                            new XElement("TC_AMOUNT", amount),
                            new XElement("BANK_PROC_TYPE", "2"),
                            new XElement("DUE_DATE", dateText),
                            new XElement("AFFECT_RISK", "1"),
                            new XElement("BN_CRDTYPE", "3"),
                            new XElement("DIVISION", "0"),
                            new XElement("COSTTYPE", "1"))))));
        return document.ToString(SaveOptions.DisableFormatting);
    }

    private (int LogicalRef, string FicheNo)? FindExistingVoucher(dynamic logo, string marker)
    {
        dynamic? query = null;
        try
        {
            query = logo.NewQuery();
            query.Statement = "SELECT TOP 1 LOGICALREF, FICHENO "
                + $"FROM LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFICHE "
                + $"WHERE CANCELLED = 0 AND GENEXP1 = '{marker}' ORDER BY LOGICALREF DESC";
            if (!(bool)query.OpenDirect() || !(bool)query.First())
            {
                return null;
            }
            return (
                Convert.ToInt32(query.FieldByName("LOGICALREF").Value),
                Convert.ToString(query.FieldByName("FICHENO").Value) ?? string.Empty);
        }
        finally
        {
            try { query?.Close(); } catch { }
            Release(query);
        }
    }

    private static string BuildMarker(string invoiceId)
    {
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(invoiceId.Trim()));
        return $"PG:{Convert.ToHexString(hash)[..32]}";
    }

    private InvoiceProcessResult Failure(string invoiceId, string marker, string error) =>
        new(false, invoiceId, _options.DryRun, false, null, null, null, null, marker, error);

    private static string BuildDataError(dynamic data, string prefix)
    {
        var parts = new List<string> { prefix };
        AddIfPresent(parts, "ErrorCode", Convert.ToString(data.ErrorCode));
        AddIfPresent(parts, "ErrorDesc", Convert.ToString(data.ErrorDesc));
        AddIfPresent(parts, "ErrorDetail", Convert.ToString(data.ErrorDescDetail));
        AddIfPresent(parts, "DBError", Convert.ToString(data.DBErrorDesc));
        try
        {
            var count = Convert.ToInt32(data.ValidateErrors.Count);
            for (var index = 0; index < count; index++)
            {
                dynamic error = data.ValidateErrors.Item(index);
                parts.Add($"Validation[{index}] ID={error.ID}: {error.Error} {error.ErrorDetail}".Trim());
            }
        }
        catch { }
        return string.Join("; ", parts);
    }

    private static void AddIfPresent(List<string> parts, string name, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value) && value != "0") parts.Add($"{name}={value}");
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
            throw new InvalidOperationException("Tiger:UserName is not configured.");
        if (string.IsNullOrWhiteSpace(_options.Password))
            throw new InvalidOperationException("Tiger:Password is not configured.");
        if (!(bool)logo.Connect())
            throw new InvalidOperationException("Logo Connect returned false.");
        if (!(bool)logo.UserLogin(_options.UserName, _options.Password))
            throw new InvalidOperationException("Logo UserLogin returned false.");
        if (!(bool)logo.CompanyLogin(_options.FirmNo, _options.PeriodNo))
            throw new InvalidOperationException(
                $"Logo CompanyLogin({_options.FirmNo}, {_options.PeriodNo}) returned false.");
        if ((int)logo.CurrentFirm != _options.FirmNo || (int)logo.CurrentPeriod != _options.PeriodNo)
            throw new InvalidOperationException(
                $"Unexpected Logo firm/period: {(int)logo.CurrentFirm}/{(int)logo.CurrentPeriod}.");
    }

    private static void LogoutAndRelease(dynamic? logo)
    {
        if (logo is null) return;
        try { logo.CompanyLogout(); } catch { }
        try { logo.UserLogout(); } catch { }
        try { logo.Disconnect(); } catch { }
        Release(logo);
    }

    private static void Release(dynamic? comObject)
    {
        if (comObject is null) return;
        try { Marshal.ReleaseComObject(comObject); } catch { }
    }
}
