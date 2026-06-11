from __future__ import annotations

from collections.abc import Sequence

from payment_gateway.providers.base import PaymentProvider


class PaymentGateway:
    def __init__(self, providers: Sequence[PaymentProvider], *, default_provider: str) -> None:
        for provider in providers:
            if not isinstance(provider, PaymentProvider):
                raise TypeError("Payment providers must inherit from PaymentProvider")
        self.providers = {provider.name: provider for provider in providers}
        self.default_provider = default_provider
        if default_provider not in self.providers:
            raise ValueError(f"Unknown default payment provider: {default_provider}")

    def provider(self, name: str | None = None) -> PaymentProvider:
        provider_name = name or self.default_provider
        try:
            return self.providers[provider_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported payment provider: {provider_name}") from exc
