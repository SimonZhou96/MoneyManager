"""Shared display-name policy. No network access."""
from functools import lru_cache
import re

from opencc import OpenCC

_CJK = re.compile(r"[\u3400-\u9fff]")
_EMPTY = {"", "nan", "none", "null", "n/a", "--", "-"}


def clean_name(value):
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() in _EMPTY else text


def is_chinese(value):
    return bool(_CJK.search(clean_name(value)))


@lru_cache(maxsize=1)
def _converter():
    return OpenCC("t2s")


def simplified(value):
    return _converter().convert(clean_name(value))


def choose_names(market, code, name_zh=None, name_en=None, previous=None):
    previous = previous or {}
    old = clean_name(previous.get("name"))
    zh = clean_name(name_zh)
    if not is_chinese(zh):
        zh = clean_name(previous.get("name_zh")) or (old if is_chinese(old) else "")
    en = clean_name(name_en)
    if is_chinese(en):
        en = ""
    en = en or clean_name(previous.get("name_en")) or (old if old and not is_chinese(old) and old != code else "")
    zh = simplified(zh) if is_chinese(zh) and market != "US" else ""
    return {"name_zh": zh or None, "name_en": en or None,
            "name": (en or code) if market == "US" else (zh or en or code)}
