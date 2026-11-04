from __future__ import annotations

from market_intel.providers.cailianpress import CailianpressIntelProvider


class NewsIntelProvider(CailianpressIntelProvider):
    """Backward-compatible alias for the original generic news provider."""

    provider_name = "news"
    name = "news"
