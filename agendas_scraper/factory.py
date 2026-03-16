from __future__ import annotations

from .base import MontanaScraper
from .config import CityScrapeConfig
from .providers import CivicClerkProvider, CustomHTMLProvider, GranicusProvider, MunicipalImpactProvider, MuniCodeProvider, PrimeGovProvider

PROVIDER_REGISTRY: dict[str, type[MontanaScraper]] = {
    "civicclerk": CivicClerkProvider,
    "granicus": GranicusProvider,
    "municipalimpact": MunicipalImpactProvider,
    "municode": MuniCodeProvider,
    "primegov": PrimeGovProvider,
    "custom_html": CustomHTMLProvider,
}


def create_provider(config: CityScrapeConfig) -> MontanaScraper:
    provider_cls = PROVIDER_REGISTRY.get(config.provider)
    if provider_cls is None:
        supported = ", ".join(sorted(PROVIDER_REGISTRY))
        raise ValueError(f"Unsupported provider '{config.provider}'. Supported providers: {supported}")
    return provider_cls(config)
