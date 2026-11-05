"""Whole-list providers; no per-security requests or market-data enrichment."""
from dataclasses import dataclass, field
import io
import re

import pandas as pd

from .names import clean_name

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
HK_EN_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
HK_ZH_URL = "https://www.hkex.com.hk/chi/services/trading/securities/securitieslists/ListOfSecurities_c.xlsx"
PHASES = (("a_sh_main", "A"), ("a_sh_star", "A"), ("a_sz", "A"),
          ("a_bj", "A"), ("a_etf", "A"), ("hk", "HK"), ("us", "US"))


@dataclass
class Snapshot:
    records: list[dict]
    warnings: list[str] = field(default_factory=list)
    source_date: str = ""


def column(frame, candidates):
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    raise ValueError(f"Missing required column {candidates}; received {list(frame.columns)}")


def parse_a(frame, exchange=None, etf=False):
    code_col = column(frame, ["证券代码", "A股代码", "代码", "code"])
    name_col = column(frame, ["证券简称", "A股简称", "名称", "name"])
    result = []
    for _, row in frame.iterrows():
        raw = clean_name(row[code_col]).upper()
        match = re.fullmatch(r"(?:(SH|SZ|BJ)[.]?)?(\d{6})", raw)
        if not match:
            raise ValueError(f"Invalid A-share/ETF code: {raw}")
        prefix, bare = match.groups()
        venue = exchange or prefix
        if etf and not venue:
            venue = "SH" if bare.startswith("5") else "SZ" if bare.startswith("1") else None
        if not venue:
            raise ValueError(f"Missing exchange for {raw}")
        result.append({"market": "A", "code": f"{venue}.{bare}", "name_zh": clean_name(row[name_col]),
                       "name_en": None, "instrument_type": "ETF" if etf else "股票", "exchange": venue})
    return result


def parse_hk(english, chinese=None):
    for required in ("Stock Code", "Name of Securities", "Category", "Sub-Category"):
        column(english, [required])
    zh_map = {}
    if chinese is not None:
        for required in ("股份代號", "股份名稱"):
            column(chinese, [required])
        zh_map = {str(row["股份代號"]).strip().zfill(5): clean_name(row["股份名稱"]) for _, row in chinese.iterrows()}
    result = []
    for _, row in english.iterrows():
        category, sub = clean_name(row["Category"]), clean_name(row["Sub-Category"])
        if category == "Equity" and sub in {"Equity Securities (Main Board)", "Equity Securities (GEM)",
                                               "Investment Companies", "Depositary Receipts"}:
            kind = "股票"
        elif category == "Exchange Traded Products" and sub in {"Exchange Traded Funds", "Leveraged and Inverse", "Leveraged and Inverse Products"}:
            kind = "ETF"
        else:
            continue
        raw = clean_name(row["Stock Code"])
        if not raw.isdigit() or len(raw) > 5:
            raise ValueError(f"Invalid HKEX code: {raw}")
        raw = raw.zfill(5)
        result.append({"market": "HK", "code": f"HK.{raw}", "name_zh": zh_map.get(raw),
                       "name_en": clean_name(row["Name of Securities"]), "instrument_type": kind,
                       "exchange": "HKEX", "original_name_zh": zh_map.get(raw)})
    return result


_NON_EQUITY = re.compile(r"\b(?:warrants?|rights|units?|ETNs?)\b|exchange[- ]traded notes?", re.I)


def parse_us(text):
    if "File Creation Time:" not in text:
        raise ValueError("Incomplete Nasdaq directory: missing creation-time footer")
    frame = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    for required in ("Symbol", "Security Name", "ETF", "Test Issue", "Listing Exchange"):
        column(frame, [required])
    result = []
    for _, row in frame.iterrows():
        if row["Test Issue"] != "N":
            continue
        # NAN is a real US ticker, not a missing-value marker.
        raw, name = str(row["Symbol"]).strip(), clean_name(row["Security Name"])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.$^/-]{0,28}", raw):
            raise ValueError(f"Invalid Nasdaq symbol: {raw}")
        if row["ETF"] not in {"Y", "N"}:
            raise ValueError(f"Unknown ETF flag for {raw}")
        if row["ETF"] == "N" and _NON_EQUITY.search(name):
            continue
        result.append({"market": "US", "code": f"US.{raw.upper()}", "name_zh": None,
                       "name_en": name, "instrument_type": "ETF" if row["ETF"] == "Y" else "股票",
                       "exchange": row["Listing Exchange"]})
    return result


class Providers:
    def __init__(self, transport):
        self.transport = transport

    def fetch(self, phase):
        if phase == "us":
            response = self.transport.request("GET", NASDAQ_URL)
            text = response.text
            return Snapshot(parse_us(text), source_date=text.split("File Creation Time:")[-1].split("|")[0].strip())
        if phase == "hk":
            response = self.transport.request("GET", HK_EN_URL)
            english = pd.read_excel(io.BytesIO(response.content), header=2, dtype=str)
            header = pd.read_excel(io.BytesIO(response.content), header=None, nrows=2)
            chinese, warnings = None, []
            try:
                response = self.transport.request("GET", HK_ZH_URL)
                chinese = pd.read_excel(io.BytesIO(response.content), header=2, dtype=str)
                if not {"股份代號", "股份名稱"}.issubset(chinese.columns):
                    raise ValueError("Invalid HKEX Chinese directory schema")
            except Exception as exc:
                warnings.append(f"Chinese directory unavailable: {type(exc).__name__}: {exc}")
            return Snapshot(parse_hk(english, chinese), warnings, str(header.iloc[1, 0]))
        import akshare as ak
        with self.transport.akshare_requests():
            if phase == "a_sh_main":
                records = parse_a(ak.stock_info_sh_name_code(symbol="主板A股"), "SH")
            elif phase == "a_sh_star":
                records = parse_a(ak.stock_info_sh_name_code(symbol="科创板"), "SH")
            elif phase == "a_sz":
                records = parse_a(ak.stock_info_sz_name_code(symbol="A股列表"), "SZ")
            elif phase == "a_bj":
                records = parse_a(ak.stock_info_bj_name_code(), "BJ")
            elif phase == "a_etf":
                records = parse_a(ak.fund_etf_category_sina(symbol="ETF基金"), etf=True)
            else:
                raise ValueError(f"Unknown catalog phase: {phase}")
        return Snapshot(records)
