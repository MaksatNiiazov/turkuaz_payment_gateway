# 1C integration sources

This directory contains source snippets and module templates for the 1C side of
PaymentGateway. Files with deployed credentials or environment-specific exports
must stay under the ignored `.local-artifacts/` directory.

- `PayQR_1C_InvoiceQRCode_Module.bsl` creates or reuses invoice QR codes.
- `PayQR_1C_PaymentSync_Module.bsl` imports successful payments into 1C.
- `PayQR_1C_LoadPayments_CommandModule.bsl` provides the manual import command.
- `PayQR_1C_ExportTigerClientCodes_*` exports Tiger client mappings.
- `PayQR_1C_Diagnostics.bsl` contains metadata diagnostics used during setup.
