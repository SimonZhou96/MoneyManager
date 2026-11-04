from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Protocol

from .models import (
    DataQuality,
    MarketSnapshot,
    OptionContractType,
    OptionQuote,
    UnderlyingSnapshot,
)


class OptionMarketDataProvider(Protocol):
    name: str

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        ...


class OptionMarketDataProviderChain:
    def __init__(self, providers: Iterable[OptionMarketDataProvider]):
        self.providers = list(providers)

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        last_error = None
        for provider in self.providers:
            try:
                snapshot = provider.fetch_snapshot(market, code)
                if snapshot.option_quotes:
                    return snapshot
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"期权行情数据不可用: {last_error}")


class FakeOptionMarketDataProvider:
    name = "fake"

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        market = str(market or "").upper()
        currency = "USD" if market == "US" else "HKD" if market == "HK" else "CNY"
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        underlying_price = 100.0
        underlying = UnderlyingSnapshot(
            market=market,
            code=code,
            name=code,
            price=underlying_price,
            currency=currency,
            quote_time=now,
            signal_summary={"方向": "偏多", "强度": 70, "波动": "中等"},
        )
        quotes: List[OptionQuote] = []
        for expiration, time_discount in (("2026-06-19", 1.0), ("2026-07-17", 1.22)):
            for strike in (90.0, 95.0, 100.0, 105.0, 110.0):
                call_mid = max(1.0, (underlying_price - strike) * 0.45 + 5.0) * time_discount
                put_mid = max(1.0, (strike - underlying_price) * 0.45 + 3.4) * time_discount
                quotes.append(_fake_quote(code, OptionContractType.CALL, strike, expiration, call_mid, currency, now))
                quotes.append(_fake_quote(code, OptionContractType.PUT, strike, expiration, put_mid, currency, now))
        return MarketSnapshot(
            snapshot_id=str(uuid.uuid4()),
            provider=self.name,
            underlying=underlying,
            option_quotes=quotes,
            data_quality=DataQuality(status="ok", warnings=["当前使用离线测试行情"], quote_time=now),
            raw_payload={"source": "fake", "说明": "离线测试行情"},
        )


class FutuOptionMarketDataProvider:
    name = "futu"

    def __init__(self, quote_ctx):
        self.quote_ctx = quote_ctx

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        try:
            import futu as ft
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"富途 API 不可用: {exc}") from exc

        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        chain_ret, chain_data = self.quote_ctx.get_option_chain(
            code,
            start="2026-01-01",
            end="2027-12-31",
            option_type=ft.OptionType.ALL,
        )
        if chain_ret != ft.RET_OK or chain_data is None or getattr(chain_data, "empty", True):
            raise RuntimeError(f"富途未返回可用期权链: {chain_data}")

        snapshot_ret, snapshot_data = self.quote_ctx.get_market_snapshot([code])
        underlying_price = None
        currency = _currency_for_market(market)
        if snapshot_ret == ft.RET_OK and snapshot_data is not None and not getattr(snapshot_data, "empty", True):
            row = snapshot_data.iloc[0]
            underlying_price = _float_or_none(row.get("last_price"))

        quotes: List[OptionQuote] = []
        for _, row in chain_data.head(80).iterrows():
            contract_type = _contract_type_from_text(row.get("option_type") or row.get("type") or row.get("code"))
            if contract_type is None:
                continue
            option_code = str(row.get("code") or row.get("option_code") or "")
            if not option_code:
                continue
            quotes.append(
                OptionQuote(
                    option_code=option_code,
                    provider_code=option_code,
                    contract_type=contract_type,
                    expiration_date=str(row.get("strike_time") or row.get("expiry_date") or ""),
                    strike=_float_or_none(row.get("strike_price")) or 0.0,
                    bid=_float_or_none(row.get("bid_price")),
                    ask=_float_or_none(row.get("ask_price")),
                    last=_float_or_none(row.get("last_price")),
                    volume=_float_or_none(row.get("volume")),
                    open_interest=_float_or_none(row.get("open_interest")),
                    implied_volatility=_float_or_none(row.get("implied_volatility")),
                    delta=_float_or_none(row.get("delta")),
                    gamma=_float_or_none(row.get("gamma")),
                    theta=_float_or_none(row.get("theta")),
                    vega=_float_or_none(row.get("vega")),
                    quote_time=now,
                    currency=currency,
                )
            )
        return MarketSnapshot(
            snapshot_id=str(uuid.uuid4()),
            provider=self.name,
            underlying=UnderlyingSnapshot(market=market, code=code, name=code, price=underlying_price, currency=currency, quote_time=now),
            option_quotes=quotes,
            data_quality=DataQuality(status="ok", warnings=[], quote_time=now),
            raw_payload={"source": "futu", "rows": len(quotes)},
        )


class YFinanceOptionMarketDataProvider:
    name = "yfinance"

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        if str(market).upper() != "US":
            raise RuntimeError("yfinance 期权链当前仅用于美股标的")
        try:
            import yfinance as yf
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"yfinance 不可用: {exc}") from exc

        symbol = code.split(".")[-1]
        ticker = yf.Ticker(symbol)
        expirations = list(getattr(ticker, "options", []) or [])
        if not expirations:
            raise RuntimeError("yfinance 未返回可用期权到期日")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        quotes: List[OptionQuote] = []
        for expiration in expirations[:2]:
            chain = ticker.option_chain(expiration)
            quotes.extend(_quotes_from_yfinance_frame(code, expiration, chain.calls, OptionContractType.CALL, now))
            quotes.extend(_quotes_from_yfinance_frame(code, expiration, chain.puts, OptionContractType.PUT, now))
        price = _float_or_none(getattr(getattr(ticker, "fast_info", None), "last_price", None))
        return MarketSnapshot(
            snapshot_id=str(uuid.uuid4()),
            provider=self.name,
            underlying=UnderlyingSnapshot(market="US", code=code, name=symbol, price=price, currency="USD", quote_time=now),
            option_quotes=quotes,
            data_quality=DataQuality(status="ok", warnings=[], quote_time=now),
            raw_payload={"source": "yfinance", "symbol": symbol, "expirations": expirations[:2]},
        )


class AKShareOptionMarketDataProvider:
    name = "akshare"

    def fetch_snapshot(self, market: str, code: str) -> MarketSnapshot:
        if str(market).upper() != "A":
            raise RuntimeError("AKShare 期权链当前仅用于 A 股 ETF/指数期权")
        try:
            import akshare as ak
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"AKShare 不可用: {exc}") from exc
        method = getattr(ak, "option_current_em", None)
        if method is None:
            raise RuntimeError("当前 AKShare 版本未提供 option_current_em")
        data = method()
        if data is None or getattr(data, "empty", True):
            raise RuntimeError("AKShare 未返回可用期权行情")
        symbol = code.split(".")[-1]
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        quotes: List[OptionQuote] = []
        for _, row in data.iterrows():
            text = "".join(str(row.get(key, "")) for key in ("代码", "名称", "合约名称", "合约代码"))
            if symbol not in text:
                continue
            contract_type = _contract_type_from_text(text)
            if contract_type is None:
                continue
            option_code = str(row.get("代码") or row.get("合约代码") or "")
            quotes.append(
                OptionQuote(
                    option_code=option_code,
                    provider_code=option_code,
                    contract_type=contract_type,
                    expiration_date=str(row.get("到期日") or row.get("行权日") or ""),
                    strike=_float_or_none(row.get("行权价")) or 0.0,
                    bid=_float_or_none(row.get("买价")),
                    ask=_float_or_none(row.get("卖价")),
                    last=_float_or_none(row.get("最新价")),
                    volume=_float_or_none(row.get("成交量")),
                    open_interest=_float_or_none(row.get("持仓量")),
                    quote_time=now,
                    currency="CNY",
                )
            )
        return MarketSnapshot(
            snapshot_id=str(uuid.uuid4()),
            provider=self.name,
            underlying=UnderlyingSnapshot(market="A", code=code, name=code, price=None, currency="CNY", quote_time=now),
            option_quotes=quotes,
            data_quality=DataQuality(status="ok", warnings=[], quote_time=now),
            raw_payload={"source": "akshare", "rows": len(quotes)},
        )


def build_default_option_market_data_provider() -> OptionMarketDataProviderChain:
    if os.getenv("OPTION_LAB_MARKET_DATA_MODE", "").strip().lower() == "fake":
        return OptionMarketDataProviderChain([FakeOptionMarketDataProvider()])
    providers: List[OptionMarketDataProvider] = []
    if os.getenv("OPTION_LAB_ENABLE_FUTU", "0").strip().lower() in {"1", "true", "yes"}:
        futu_provider = _try_build_futu_provider()
        if futu_provider is not None:
            providers.append(futu_provider)
    providers.extend([YFinanceOptionMarketDataProvider(), AKShareOptionMarketDataProvider()])
    if os.getenv("OPTION_LAB_ALLOW_FAKE_FALLBACK", "1").strip().lower() not in {"0", "false", "no"}:
        providers.append(FakeOptionMarketDataProvider())
    return OptionMarketDataProviderChain(providers)


def _try_build_futu_provider() -> Optional[FutuOptionMarketDataProvider]:
    try:
        from kline_fetcher import _ready_opend_quote_context
    except Exception:
        return None
    quote_ctx = _ready_opend_quote_context()
    return FutuOptionMarketDataProvider(quote_ctx) if quote_ctx is not None else None


def _fake_quote(code: str, contract_type: OptionContractType, strike: float, expiration: str, mid: float, currency: str, now: str) -> OptionQuote:
    suffix = "CALL" if contract_type == OptionContractType.CALL else "PUT"
    spread = max(mid * 0.04, 0.05)
    return OptionQuote(
        option_code=f"{code}.{suffix}.{int(strike)}.{expiration}",
        provider_code=f"{code}-{suffix[0]}-{int(strike)}-{expiration}",
        contract_type=contract_type,
        expiration_date=expiration,
        strike=strike,
        bid=round(mid - spread / 2, 4),
        ask=round(mid + spread / 2, 4),
        last=round(mid, 4),
        volume=900 + int(abs(100 - strike) * 20),
        open_interest=4200 + int(abs(100 - strike) * 100),
        implied_volatility=0.28 + abs(100 - strike) / 1000,
        delta=0.5 if contract_type == OptionContractType.CALL else -0.5,
        gamma=0.04,
        theta=-0.03,
        vega=0.1,
        quote_time=now,
        currency=currency,
    )


def _quotes_from_yfinance_frame(code: str, expiration: str, frame, contract_type: OptionContractType, now: str) -> List[OptionQuote]:
    quotes: List[OptionQuote] = []
    if frame is None or getattr(frame, "empty", True):
        return quotes
    for _, row in frame.head(40).iterrows():
        option_code = str(row.get("contractSymbol") or "")
        strike = _float_or_none(row.get("strike"))
        if not option_code or strike is None:
            continue
        quotes.append(
            OptionQuote(
                option_code=option_code,
                provider_code=option_code,
                contract_type=contract_type,
                expiration_date=expiration,
                strike=strike,
                bid=_float_or_none(row.get("bid")),
                ask=_float_or_none(row.get("ask")),
                last=_float_or_none(row.get("lastPrice")),
                volume=_float_or_none(row.get("volume")),
                open_interest=_float_or_none(row.get("openInterest")),
                implied_volatility=_float_or_none(row.get("impliedVolatility")),
                quote_time=now,
                currency="USD",
            )
        )
    return quotes


def _contract_type_from_text(value) -> Optional[OptionContractType]:
    text = str(value or "").upper()
    if "CALL" in text or "认购" in text or text.endswith("C"):
        return OptionContractType.CALL
    if "PUT" in text or "认沽" in text or text.endswith("P"):
        return OptionContractType.PUT
    return None


def _currency_for_market(market: str) -> str:
    value = str(market or "").upper()
    if value == "US":
        return "USD"
    if value == "HK":
        return "HKD"
    return "CNY"


def _float_or_none(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
