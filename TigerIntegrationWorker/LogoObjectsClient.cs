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

        var documentDate = ResolveDocumentDate(invoice);
        var lineMarker = BuildLineMarker(invoice.InvoiceId);
        var groupMarker = BuildGroupMarker(invoice.TargetBankAccountCode!, documentDate);

        lock (_comLock)
        {
            object? logo = null;
            try
            {
                logo = CreateUnityApplication();
                ConnectAndLogin(logo);

                if (!_options.DryRun)
                {
                    EnsureWriteIsAllowed();
                    var existingLine = FindExistingInvoiceLine(logo, lineMarker);
                    if (existingLine is not null)
                    {
                        return Success(
                            invoice,
                            false,
                            true,
                            existingLine.Value.VoucherRef,
                            existingLine.Value.FicheNo,
                            null,
                            null,
                            lineMarker);
                    }
                }

                var existingGroup = _options.DryRun ? null : FindExistingGroupVoucher(logo, groupMarker);
                if (existingGroup is null)
                {
                    return CreateGroupedVoucher(logo, invoice, documentDate, groupMarker, lineMarker);
                }

                return AppendToGroupedVoucher(
                    logo,
                    invoice,
                    existingGroup.Value.VoucherRef,
                    existingGroup.Value.FicheNo,
                    documentDate,
                    groupMarker,
                    lineMarker);
            }
            catch (Exception ex)
            {
                if (!_options.DryRun && logo is not null)
                {
                    try
                    {
                        var existingLine = FindExistingInvoiceLine(logo, lineMarker);
                        if (existingLine is not null)
                        {
                            return Success(
                                invoice,
                                false,
                                true,
                                existingLine.Value.VoucherRef,
                                existingLine.Value.FicheNo,
                                null,
                                null,
                                lineMarker);
                        }
                    }
                    catch
                    {
                        // Preserve the original failure; this is only a post-failure idempotency probe.
                    }
                }

                return Failure(invoice.InvoiceId, lineMarker, ex.Message);
            }
            finally
            {
                LogoutAndRelease(logo);
            }
        }
    }

    private InvoiceProcessResult CreateGroupedVoucher(
        dynamic logo,
        InvoicePaidEvent invoice,
        DateOnly documentDate,
        string groupMarker,
        string lineMarker)
    {
        dynamic? data = null;
        try
        {
            var xml = BuildBankVoucherInsertXml(invoice, documentDate, groupMarker, lineMarker);
            data = logo.NewDataObject(BankVoucherDataObjectType);
            if (!(bool)data.ImportFromXmlStr("BANK_VOUCHERS", xml))
            {
                return Failure(invoice.InvoiceId, lineMarker, BuildDataError(data, "XML import failed"));
            }

            var importedLineCount = Convert.ToInt32(
                data.DataFields.FieldByName("TRANSACTIONS").Lines.Count,
                CultureInfo.InvariantCulture);
            if (importedLineCount != 1)
            {
                return Failure(invoice.InvoiceId, lineMarker, $"Expected one imported line, got {importedLineCount}.");
            }

            if (_options.DryRun)
            {
                return Success(
                    invoice,
                    true,
                    false,
                    null,
                    null,
                    importedLineCount,
                    null,
                    lineMarker);
            }

            if (!(bool)data.Post())
            {
                return Failure(invoice.InvoiceId, lineMarker, BuildDataError(data, "Tiger Post returned false"));
            }

            var logicalRef = Convert.ToInt32(
                data.DataFields.FieldByName("INTERNAL_REFERENCE").Value,
                CultureInfo.InvariantCulture);
            var ficheNo = Convert.ToString(
                data.DataFields.FieldByName("NUMBER").Value,
                CultureInfo.InvariantCulture);

            var verification = VerifyLinePersisted(logo, logicalRef, lineMarker, expectedAmount: invoice.Amount);
            if (!verification.Found)
            {
                throw new InvalidOperationException(
                    $"Voucher {logicalRef} was posted but line marker {lineMarker} was not found.");
            }

            return Success(
                invoice,
                false,
                false,
                logicalRef,
                ficheNo,
                verification.VoucherLineCount,
                null,
                lineMarker);
        }
        finally
        {
            Release(data);
        }
    }

    private InvoiceProcessResult AppendToGroupedVoucher(
        dynamic logo,
        InvoicePaidEvent invoice,
        int voucherRef,
        string ficheNo,
        DateOnly documentDate,
        string groupMarker,
        string lineMarker)
    {
        var existingLine = FindExistingInvoiceLine(logo, lineMarker);
        if (existingLine is not null)
        {
            return Success(
                invoice,
                false,
                true,
                existingLine.Value.VoucherRef,
                existingLine.Value.FicheNo,
                null,
                null,
                lineMarker);
        }

        var before = ReadVoucherSummary(logo, voucherRef);
        if (!string.Equals(before.GroupMarker, groupMarker, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Voucher {voucherRef} marker mismatch. Expected {groupMarker}, got {before.GroupMarker}.");
        }

        dynamic? readData = null;
        dynamic? updateData = null;
        string? exportPath = null;
        try
        {
            readData = logo.NewDataObject(BankVoucherDataObjectType);
            if (!(bool)readData.Read(voucherRef))
            {
                throw new InvalidOperationException($"Read failed for voucher {voucherRef}.");
            }

            exportPath = Path.Combine(
                Path.GetTempPath(),
                $"pg-bank-voucher-{voucherRef}-{DateTime.Now:yyyyMMddHHmmssfff}.xml");
            if (!(bool)readData.ExportToXML("BANK_VOUCHERS", exportPath))
            {
                throw new InvalidOperationException(BuildDataError(readData, "ExportToXML failed"));
            }

            var xml = LoadTigerExportXml(exportPath);
            PrepareExportedXmlForAppend(xml, invoice, documentDate, before, groupMarker, lineMarker);

            var duplicateAfterExport = FindExistingInvoiceLine(logo, lineMarker);
            if (duplicateAfterExport is not null)
            {
                return Success(
                    invoice,
                    false,
                    true,
                    duplicateAfterExport.Value.VoucherRef,
                    duplicateAfterExport.Value.FicheNo,
                    null,
                    null,
                    lineMarker);
            }

            updateData = logo.NewDataObject(BankVoucherDataObjectType);
            if (!(bool)updateData.ImportFromXmlStr("BANK_VOUCHERS", xml.ToString(SaveOptions.DisableFormatting)))
            {
                throw new InvalidOperationException(BuildDataError(updateData, "Append XML import failed"));
            }

            var importedLineCount = Convert.ToInt32(
                updateData.DataFields.FieldByName("TRANSACTIONS").Lines.Count,
                CultureInfo.InvariantCulture);
            if (importedLineCount != before.LineCount + 1)
            {
                throw new InvalidOperationException(
                    $"Expected imported append XML to contain {before.LineCount + 1} lines, got {importedLineCount}.");
            }

            var latestBeforePost = ReadVoucherSummary(logo, voucherRef);
            if (latestBeforePost.LineCount != before.LineCount ||
                latestBeforePost.LineAmountSum != before.LineAmountSum)
            {
                throw new InvalidOperationException(
                    $"Voucher {voucherRef} changed during append. "
                    + $"Exported count/sum={before.LineCount}/{before.LineAmountSum}; "
                    + $"latest count/sum={latestBeforePost.LineCount}/{latestBeforePost.LineAmountSum}. Retry with a fresh export.");
            }

            if (!(bool)updateData.Post())
            {
                throw new InvalidOperationException(BuildDataError(updateData, "Append Post failed"));
            }

            var after = ReadVoucherSummary(logo, voucherRef);
            var expectedTotal = before.LineAmountSum + invoice.Amount;
            if (after.LineCount != before.LineCount + 1)
            {
                throw new InvalidOperationException(
                    $"Append line count verification failed for voucher {voucherRef}. Before={before.LineCount}, after={after.LineCount}.");
            }
            if (after.LineAmountSum != expectedTotal)
            {
                throw new InvalidOperationException(
                    $"Append amount verification failed for voucher {voucherRef}. Expected total={expectedTotal}, actual={after.LineAmountSum}.");
            }

            var verification = VerifyLinePersisted(logo, voucherRef, lineMarker, expectedAmount: invoice.Amount);
            if (!verification.Found)
            {
                throw new InvalidOperationException(
                    $"Append posted but line marker {lineMarker} was not found in voucher {voucherRef}.");
            }

            return Success(
                invoice,
                false,
                false,
                voucherRef,
                ficheNo,
                after.LineCount,
                null,
                lineMarker);
        }
        finally
        {
            if (!string.IsNullOrWhiteSpace(exportPath))
            {
                try { File.Delete(exportPath); } catch { }
            }
            Release(updateData);
            Release(readData);
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

    private static string BuildBankVoucherInsertXml(
        InvoicePaidEvent invoice,
        DateOnly date,
        string groupMarker,
        string lineMarker)
    {
        var dateText = FormatDate(date);
        var amount = FormatAmount(invoice.Amount);
        var document = new XDocument(
            new XElement("BANK_VOUCHERS",
                new XElement("BANK_VOUCHER",
                    new XAttribute("DBOP", "INS"),
                    new XElement("DATE", dateText),
                    new XElement("TYPE", "3"),
                    new XElement("TOTAL_DEBIT", amount),
                    new XElement("NOTES1", groupMarker),
                    new XElement("CURRSEL_TOTALS", "1"),
                    new XElement("DIVISION", "0"),
                    new XElement("DEPARMENT", "0"),
                    new XElement("TRANSACTIONS",
                        BuildTransactionElement(invoice, date, lineMarker)))));
        return document.ToString(SaveOptions.DisableFormatting);
    }

    private static XElement BuildTransactionElement(InvoicePaidEvent invoice, DateOnly date, string lineMarker)
    {
        var dateText = FormatDate(date);
        var amount = FormatAmount(invoice.Amount);
        var bankAccountCode = CleanTigerCode(invoice.TargetBankAccountCode!);
        var clientCode = CleanTigerCode(invoice.ClientCode!);
        return new XElement("TRANSACTION",
            new XElement("TYPE", "1"),
            new XElement("BANKACC_CODE", bankAccountCode),
            new XElement("ARP_CODE", clientCode),
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
            new XElement("COSTTYPE", "1"),
            new XElement("DESCRIPTION", lineMarker));
    }

    private static void PrepareExportedXmlForAppend(
        XDocument xml,
        InvoicePaidEvent invoice,
        DateOnly date,
        VoucherSummary before,
        string groupMarker,
        string lineMarker)
    {
        var voucher = xml.Root?.Element("BANK_VOUCHER")
            ?? throw new InvalidOperationException("Exported XML does not contain BANK_VOUCHER.");
        voucher.SetAttributeValue("DBOP", "UPD");
        SetElement(voucher, "DATA_REFERENCE", before.LogicalRef.ToString(CultureInfo.InvariantCulture));
        SetElement(voucher, "TYPE", "3");
        SetElement(voucher, "TOTAL_DEBIT", FormatAmount(before.LineAmountSum + invoice.Amount));
        SetElement(voucher, "NOTES1", groupMarker);

        var transactions = voucher.Element("TRANSACTIONS")
            ?? throw new InvalidOperationException("Exported XML does not contain TRANSACTIONS.");
        var template = transactions.Elements("TRANSACTION").FirstOrDefault()
            ?? throw new InvalidOperationException("Exported XML does not contain a template TRANSACTION.");

        var newLine = new XElement(template);
        foreach (var generatedName in GeneratedLineElementNames)
        {
            newLine.Element(generatedName)?.Remove();
        }

        ApplyTransactionValues(newLine, invoice, date, lineMarker);
        transactions.Add(newLine);
    }

    private static readonly string[] GeneratedLineElementNames =
    [
        "INTERNAL_REFERENCE",
        "DATA_REFERENCE",
        "SOURCEFREF",
        "TRANNO",
        "GUID",
        "ORGLOGOID",
        "PAYMENT_LIST",
        "DEFNFLDSLIST",
        "PREACCLINES"
    ];

    private static void ApplyTransactionValues(
        XElement transaction,
        InvoicePaidEvent invoice,
        DateOnly date,
        string lineMarker)
    {
        var dateText = FormatDate(date);
        var amount = FormatAmount(invoice.Amount);
        var bankAccountCode = CleanTigerCode(invoice.TargetBankAccountCode!);
        var clientCode = CleanTigerCode(invoice.ClientCode!);
        SetElement(transaction, "TYPE", "1");
        SetElement(transaction, "BANKACC_CODE", bankAccountCode);
        SetElement(transaction, "ARP_CODE", clientCode);
        SetElement(transaction, "DATE", dateText);
        SetElement(transaction, "TRCODE", "3");
        SetElement(transaction, "MODULENR", "7");
        SetElement(transaction, "CURR_TRANS", "37");
        SetElement(transaction, "DEBIT", amount);
        SetElement(transaction, "AMOUNT", amount);
        SetElement(transaction, "TC_XRATE", "1");
        SetElement(transaction, "TC_AMOUNT", amount);
        SetElement(transaction, "BANK_PROC_TYPE", "2");
        SetElement(transaction, "DUE_DATE", dateText);
        SetElement(transaction, "AFFECT_RISK", "1");
        SetElement(transaction, "BN_CRDTYPE", "3");
        SetElement(transaction, "DIVISION", "0");
        SetElement(transaction, "COSTTYPE", "1");
        SetElement(transaction, "DESCRIPTION", lineMarker);
    }

    private (int VoucherRef, string FicheNo)? FindExistingGroupVoucher(dynamic logo, string groupMarker)
    {
        dynamic? query = null;
        try
        {
            query = logo.NewQuery();
            query.Statement = "SELECT TOP 1 LOGICALREF, FICHENO "
                + $"FROM LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFICHE "
                + $"WHERE CANCELLED = 0 AND GENEXP1 = '{EscapeSqlLiteral(groupMarker)}' "
                + "ORDER BY LOGICALREF DESC";
            if (!(bool)query.OpenDirect() || !(bool)query.First())
            {
                return null;
            }
            return (
                Convert.ToInt32(query.FieldByName("LOGICALREF").Value, CultureInfo.InvariantCulture),
                Convert.ToString(query.FieldByName("FICHENO").Value, CultureInfo.InvariantCulture) ?? string.Empty);
        }
        finally
        {
            try { query?.Close(); } catch { }
            Release(query);
        }
    }

    private (int VoucherRef, string FicheNo)? FindExistingInvoiceLine(dynamic logo, string lineMarker)
    {
        dynamic? query = null;
        try
        {
            var escapedMarker = EscapeSqlLiteral(lineMarker);
            query = logo.NewQuery();
            query.Statement = "SELECT TOP 1 H.LOGICALREF, H.FICHENO "
                + $"FROM LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFICHE AS H "
                + $"LEFT JOIN LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFLINE AS L "
                + "ON L.SOURCEFREF = H.LOGICALREF AND L.CANCELLED = 0 "
                + "WHERE H.CANCELLED = 0 "
                + $"AND (H.GENEXP1 = '{escapedMarker}' OR L.LINEEXP = '{escapedMarker}') "
                + "ORDER BY H.LOGICALREF DESC";
            if (!(bool)query.OpenDirect() || !(bool)query.First())
            {
                return null;
            }
            return (
                Convert.ToInt32(query.FieldByName("LOGICALREF").Value, CultureInfo.InvariantCulture),
                Convert.ToString(query.FieldByName("FICHENO").Value, CultureInfo.InvariantCulture) ?? string.Empty);
        }
        finally
        {
            try { query?.Close(); } catch { }
            Release(query);
        }
    }

    private VoucherSummary ReadVoucherSummary(dynamic logo, int voucherRef)
    {
        dynamic? query = null;
        try
        {
            query = logo.NewQuery();
            query.Statement = $"""
SELECT
    H.LOGICALREF AS FICHE_REF,
    H.FICHENO,
    H.DEBITTOT,
    H.GENEXP1,
    COUNT(L.LOGICALREF) AS LINE_COUNT,
    COALESCE(SUM(L.AMOUNT), 0) AS LINE_AMOUNT_SUM
FROM LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFICHE AS H
LEFT JOIN LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFLINE AS L
    ON L.SOURCEFREF = H.LOGICALREF AND L.CANCELLED = 0
WHERE H.LOGICALREF = {voucherRef} AND H.CANCELLED = 0
GROUP BY H.LOGICALREF, H.FICHENO, H.DEBITTOT, H.GENEXP1
""";
            if (!(bool)query.OpenDirect())
            {
                throw new InvalidOperationException("OpenDirect failed for voucher summary query.");
            }
            if (!(bool)query.First())
            {
                throw new InvalidOperationException($"Voucher {voucherRef} was not found.");
            }

            return new VoucherSummary(
                Convert.ToInt32(query.FieldByName("FICHE_REF").Value, CultureInfo.InvariantCulture),
                Convert.ToString(query.FieldByName("FICHENO").Value, CultureInfo.InvariantCulture) ?? string.Empty,
                Convert.ToDecimal(query.FieldByName("DEBITTOT").Value, CultureInfo.InvariantCulture),
                Convert.ToString(query.FieldByName("GENEXP1").Value, CultureInfo.InvariantCulture) ?? string.Empty,
                Convert.ToInt32(query.FieldByName("LINE_COUNT").Value, CultureInfo.InvariantCulture),
                Convert.ToDecimal(query.FieldByName("LINE_AMOUNT_SUM").Value, CultureInfo.InvariantCulture));
        }
        finally
        {
            try { query?.Close(); } catch { }
            Release(query);
        }
    }

    private LineVerification VerifyLinePersisted(dynamic logo, int voucherRef, string lineMarker, decimal expectedAmount)
    {
        dynamic? query = null;
        try
        {
            query = logo.NewQuery();
            query.Statement = $"""
SELECT
    H.LOGICALREF AS FICHE_REF,
    H.FICHENO,
    COUNT(ALL_LINES.LOGICALREF) AS VOUCHER_LINE_COUNT,
    L.LOGICALREF AS LINE_REF,
    L.AMOUNT
FROM LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFICHE AS H
LEFT JOIN LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFLINE AS ALL_LINES
    ON ALL_LINES.SOURCEFREF = H.LOGICALREF AND ALL_LINES.CANCELLED = 0
LEFT JOIN LG_{_options.FirmNo:000}_{_options.PeriodNo:00}_BNFLINE AS L
    ON L.SOURCEFREF = H.LOGICALREF
    AND L.CANCELLED = 0
    AND L.LINEEXP = '{EscapeSqlLiteral(lineMarker)}'
WHERE H.LOGICALREF = {voucherRef} AND H.CANCELLED = 0
GROUP BY H.LOGICALREF, H.FICHENO, L.LOGICALREF, L.AMOUNT
ORDER BY L.LOGICALREF DESC
""";
            if (!(bool)query.OpenDirect() || !(bool)query.First())
            {
                return new LineVerification(false, null, null, null);
            }

            var lineRefValue = query.FieldByName("LINE_REF").Value;
            if (lineRefValue is null || lineRefValue is DBNull)
            {
                return new LineVerification(
                    false,
                    Convert.ToInt32(query.FieldByName("VOUCHER_LINE_COUNT").Value, CultureInfo.InvariantCulture),
                    null,
                    null);
            }

            var amount = Convert.ToDecimal(query.FieldByName("AMOUNT").Value, CultureInfo.InvariantCulture);
            if (amount != expectedAmount)
            {
                throw new InvalidOperationException(
                    $"Line marker {lineMarker} amount mismatch. Expected={expectedAmount}, actual={amount}.");
            }

            return new LineVerification(
                true,
                Convert.ToInt32(query.FieldByName("VOUCHER_LINE_COUNT").Value, CultureInfo.InvariantCulture),
                Convert.ToInt32(query.FieldByName("LINE_REF").Value, CultureInfo.InvariantCulture),
                null);
        }
        finally
        {
            try { query?.Close(); } catch { }
            Release(query);
        }
    }

    private static XDocument LoadTigerExportXml(string path)
    {
        var bytes = File.ReadAllBytes(path);
        var text = new string(bytes.Select(value => (char)value).ToArray());
        return XDocument.Parse(text, LoadOptions.PreserveWhitespace);
    }

    private static void SetElement(XElement parent, string name, string value)
    {
        var element = parent.Element(name);
        if (element is null)
        {
            parent.Add(new XElement(name, value));
            return;
        }
        element.Value = value;
    }

    private static string BuildLineMarker(string invoiceId)
    {
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(invoiceId.Trim()));
        return $"PG:{Convert.ToHexString(hash)[..32]}";
    }

    private static string BuildGroupMarker(string bankAccountCode, DateOnly date)
    {
        var normalizedBankAccountCode = CleanTigerCode(bankAccountCode).ToUpperInvariant();
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(normalizedBankAccountCode));
        return $"PGG:{date:yyyyMMdd}:{Convert.ToHexString(hash)[..16]}";
    }

    private static string CleanTigerCode(string value) =>
        string.Join(
            " ",
            value.Trim().Split(
                new[] { ' ', '\t', '\r', '\n' },
                StringSplitOptions.RemoveEmptyEntries));

    private InvoiceProcessResult Success(
        InvoicePaidEvent invoice,
        bool dryRun,
        bool alreadyExists,
        int? logicalRef,
        string? ficheNo,
        int? savedLineCount,
        int? paymentListCount,
        string lineMarker) =>
        new(
            true,
            invoice.InvoiceId,
            dryRun,
            alreadyExists,
            logicalRef,
            ficheNo,
            savedLineCount,
            paymentListCount,
            lineMarker,
            null);

    private InvoiceProcessResult Failure(string invoiceId, string marker, string error) =>
        new(false, invoiceId, _options.DryRun, false, null, null, null, null, marker, error);

    private static string BuildDataError(dynamic data, string prefix)
    {
        var parts = new List<string> { prefix };
        AddIfPresent(parts, "ErrorCode", Convert.ToString(data.ErrorCode, CultureInfo.InvariantCulture));
        AddIfPresent(parts, "ErrorDesc", Convert.ToString(data.ErrorDesc, CultureInfo.InvariantCulture));
        AddIfPresent(parts, "ErrorDetail", Convert.ToString(data.ErrorDescDetail, CultureInfo.InvariantCulture));
        AddIfPresent(parts, "DBError", Convert.ToString(data.DBErrorDesc, CultureInfo.InvariantCulture));
        try
        {
            var count = Convert.ToInt32(data.ValidateErrors.Count, CultureInfo.InvariantCulture);
            for (var index = 0; index < count; index++)
            {
                dynamic error = data.ValidateErrors.Item(index);
                parts.Add($"Validation[{index}] ID={error.ID}: {error.Error} {error.ErrorDetail}".Trim());
            }
        }
        catch
        {
            // ValidateErrors is best-effort diagnostic output.
        }
        return string.Join("; ", parts);
    }

    private static void AddIfPresent(List<string> parts, string name, string? value)
    {
        if (!string.IsNullOrWhiteSpace(value) && value != "0") parts.Add($"{name}={value}");
    }

    private static string FormatDate(DateOnly date) =>
        date.ToString("dd/MM/yyyy", CultureInfo.InvariantCulture);

    private static string FormatAmount(decimal amount) =>
        amount.ToString("0.##", CultureInfo.InvariantCulture);

    private static string EscapeSqlLiteral(string value) =>
        value.Replace("'", "''", StringComparison.Ordinal);

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

    private sealed record VoucherSummary(
        int LogicalRef,
        string FicheNo,
        decimal HeaderDebit,
        string GroupMarker,
        int LineCount,
        decimal LineAmountSum);

    private sealed record LineVerification(
        bool Found,
        int? VoucherLineCount,
        int? LineRef,
        int? PaymentListCount);
}
