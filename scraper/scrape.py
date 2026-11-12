#!/usr/bin/env python3
"""Збирач оголошень про продаж квартир (OLX, DOM.RIA, rieltor.ua, mistechko.com, svitkvartyr.com).

Запуск:  python scraper/scrape.py                      # усі джерела з config.json
         python scraper/scrape.py --sources olx,rieltor  # лише вибрані
         python scraper/scrape.py --debug               # зберігає сирі сторінки в debug/

Результат: docs/data/listings.json (дані для сайту) і new_listings.md (нові варіанти
для сповіщення; файл порожній, якщо нічого нового).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "docs" / "data" / "listings.json"
GEOCACHE_PATH = ROOT / "docs" / "data" / "geocache.json"
NEW_PATH = ROOT / "new_listings.md"
DEBUG_DIR = ROOT / "debug"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.6",
}

DEBUG = False
TODAY = dt.date.today().isoformat()
CFG: dict = {}
KNOWN: dict = {}


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


CURL_HOSTS = ("olx.ua", "rieltor.ua")  # ці сайти відкидають запити бібліотеки requests, але пускають curl


def get_curl(url: str, params: dict | None, as_json: bool, tries: int):
    import subprocess
    import urllib.parse
    full = url + (("&" if "?" in url else "?") + urllib.parse.urlencode(params) if params else "")
    exe = "curl.exe" if os.name == "nt" else "curl"
    for attempt in range(1, tries + 1):
        try:
            p = subprocess.run(
                [exe, "-sL", "--compressed", "--max-time", "40", "-A", HEADERS["User-Agent"],
                 "-H", "Accept-Language: " + HEADERS["Accept-Language"],
                 "-H", "Accept: " + ("application/json" if as_json else "text/html,application/xhtml+xml"),
                 "-w", "\n%{http_code}", full],
                capture_output=True, timeout=60)
            body, _, code = p.stdout.decode("utf-8", "replace").rpartition("\n")
            code = int(code.strip() or 0)
            if code in (403, 404, 410):
                log(f"  ! {full}: HTTP {code} (curl) — пропускаю")
                return None
            if code == 429 or code >= 500 or code == 0:
                log(f"  ! {full}: HTTP {code} (curl, спроба {attempt})")
                time.sleep((30 if code == 429 else 5) * attempt)
                continue
            if DEBUG:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / (hashlib.md5(full.encode()).hexdigest()[:10] + ".html")).write_text(body, encoding="utf-8")
            return json.loads(body) if as_json else body
        except Exception as e:  # noqa: BLE001
            log(f"  ! {full} (curl, спроба {attempt}): {e}")
            time.sleep(3 * attempt)
    return None


def get(url: str, *, params: dict | None = None, as_json: bool = False, tries: int = 3, headers: dict | None = None):
    if headers is None and any(h in url for h in CURL_HOSTS):
        return get_curl(url, params, as_json, tries)
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, headers=headers or HEADERS, timeout=30)
            if r.status_code in (403, 404, 410):
                log(f"  ! {r.url}: HTTP {r.status_code} — пропускаю")
                return None
            if r.status_code == 429:
                time.sleep(10 * attempt)
                continue
            r.raise_for_status()
            if DEBUG:
                DEBUG_DIR.mkdir(exist_ok=True)
                name = hashlib.md5((url + json.dumps(params or {})).encode()).hexdigest()[:10]
                (DEBUG_DIR / f"{name}.{'json' if as_json else 'html'}").write_text(r.text, encoding="utf-8")
            return r.json() if as_json else r.text
        except Exception as e:  # noqa: BLE001
            log(f"  ! {url} (спроба {attempt}): {e}")
            time.sleep(3 * attempt)
    return None


# ================================================================ helpers

def num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(" ", " ").replace(" ", " ")
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s).replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def to_usd(value: float | None, currency: str, rate: float) -> int | None:
    if not value:
        return None
    c = (currency or "").upper()
    if c in ("USD", "$"):
        return round(value)
    if c in ("EUR", "€"):
        return round(value * 1.08)
    return round(value / rate)


def money(text: str, rate: float) -> int | None:
    """'139 000 $' / '6 727 500 грн.' / '150 тис євро' → долари."""
    if not text:
        return None
    v = num(text)
    if not v:
        return None
    t = text.lower()
    cur = "USD" if "$" in t or "usd" in t or "дол" in t else "EUR" if "€" in t or "євро" in t or "eur" in t else "UAH"
    return to_usd(v, cur, rate)


def usd_rate() -> float:
    """Курс НБУ гривні до долара; якщо НБУ не відповідає — курс з попереднього запуску."""
    data = get("https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange",
               params={"valcode": "USD", "json": ""}, as_json=True)
    try:
        return float(data[0]["rate"])
    except Exception:  # noqa: BLE001
        try:
            prev = float(json.loads(DATA_PATH.read_text(encoding="utf-8")).get("usd_rate"))
        except Exception:  # noqa: BLE001
            prev = 46.0
        log(f"  ! не вдалося отримати курс НБУ, беру попередній {prev}")
        return prev


def clean(text: str | None, limit: int = 2000) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()[:limit]


OLD_FUND_RE = re.compile(
    r"австр|польськ|довоєн|старий фонд|стар(ий|а|ому) цегл|кам['’]?яниц|сталінк|"
    r"висок\w* стел|стел\w*\s*(3[.,]\d|3\.?\d?\s*м|[34][.,]\d)|"
    r"\b(18[5-9]\d|19[0-3]\d)\s*(р|рік|року)|до\s*19[0-4]\d\s*р|царськ",
    re.I,
)
REPAIR_RE = re.compile(
    r"під ремонт|без ремонту|потребує ремонту|старий ремонт|житловий стан|"
    r"косметичн\w* ремонт|радянськ\w* ремонт|під чистов|після будівельн|під оздоблення|"
    r"ремонт на свій смак|бабусин",
    re.I,
)
FRESH_RE = re.compile(r"євроремонт|дизайнерськ\w* ремонт|авторськ\w* (ремонт|проєкт|проект)|новий ремонт|свіжий ремонт", re.I)
OWNER_RE = re.compile(r"від власник|без посередник|без комісі|без ріелтор|без рієлтор|без ан\b|власник", re.I)
AGENCY_RE = re.compile(r"агенц|ріелтор\s+\w+|рієлтор\s+\w+|комісія\s+\d|\bан\s+[«\"']", re.I)
APART_RE = re.compile(r"апартамент", re.I)
UNDER_CONSTR_RE = re.compile(r"переуступк|будується|здача (в|у)?\s*(експлуатац|\d)|0 цикл", re.I)
NEWBUILD_RE = re.compile(r"новобуд|первинн|primary_market|житловий фонд від 20[12]\d|\bЖК\b|переуступк|здача в експлуатац|0 цикл|від забудовника", re.I)
READY_RE = re.compile(r"здан\w*|введен\w* в експлуатац|право власност|заселен", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?3\s?8\s?)?\(?0\s?\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")


UA_MONTHS = {"січ": 1, "лют": 2, "бер": 3, "квіт": 4, "трав": 5, "черв": 6, "лип": 7, "серп": 8, "верес": 9, "жовт": 10, "листоп": 11, "груд": 12,
             "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}


def parse_date(text: str | None) -> str | None:
    """'вчора', '3 дні тому', '6 листопада', 'Опубликовано 9 ноя.', '11/11/2026' → 'YYYY-MM-DD'."""
    if not text:
        return None
    t = text.lower()
    today = dt.date.today()
    if re.search(r"сьогодні|сегодня|годин\w* тому|час\w* назад|хвилин|минут", t):
        return today.isoformat()
    if re.search(r"вчора|вчера", t):
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*(дн|день|дні|днів|тиж|нед|міс|мес)", t)
    if m:
        n = int(m.group(1)); unit = m.group(2)
        days = n * (7 if unit.startswith(("тиж", "нед")) else 30 if unit.startswith(("міс", "мес")) else 1)
        return (today - dt.timedelta(days=days)).isoformat()
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", t)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})\s+([а-яіїє]{3,})\.?\s*(\d{4})?", t)
    if m:
        mon = next((v for k, v in UA_MONTHS.items() if m.group(2).startswith(k)), None)
        if mon:
            year = int(m.group(3)) if m.group(3) else today.year
            try:
                d = dt.date(year, mon, int(m.group(1)))
            except ValueError:
                return None
            if d > today:
                d = d.replace(year=d.year - 1)
            return d.isoformat()
    return None


def phones_from_text(text: str) -> list[str]:
    out = []
    for m in PHONE_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group())[-10:]
        if len(digits) == 10:
            out.append(f"+38 {digits[:3]} {digits[3:6]} {digits[6:8]} {digits[8:]}")
    return list(dict.fromkeys(out))


def make_flags(item: dict, cfg: dict) -> dict:
    text = " ".join(str(item.get(k) or "") for k in ("title", "description", "building", "condition"))
    year = item.get("year_built")
    flags = {
        "old_fund": bool(OLD_FUND_RE.search(text)) or (year is not None and 1700 < year < 1945),
        "needs_repair": bool(REPAIR_RE.search(text)),
        "fresh_renovation": bool(FRESH_RE.search(text)),
        "owner": item.get("is_owner") if item.get("is_owner") is not None else bool(OWNER_RE.search(text)),
        "apartments": bool(APART_RE.search(text)),
        "under_construction": bool(UNDER_CONSTR_RE.search(text)),
        "spacious": (item.get("area") or 0) >= cfg["spacious_area_m2"],
        "new_build": bool(NEWBUILD_RE.search(text)) or (year is not None and year >= 2010),
        "ready": bool(READY_RE.search(text)) and not bool(UNDER_CONSTR_RE.search(text)),
        "over_budget": (item.get("price_usd") or 0) > cfg["budget_usd"],
    }
    if flags["owner"] and item.get("is_owner") is None and AGENCY_RE.search(text):
        flags["owner"] = False
    return flags


def score(item: dict) -> float:
    f = item["flags"]
    s = 0.0
    s += 3 if not f["over_budget"] else 0
    s += 3 if f["old_fund"] else 0
    s += 2 if f["needs_repair"] else 0
    s += 2 if f["owner"] else 0
    s += 1 if f["spacious"] else 0
    s -= 3 if f["apartments"] else 0
    s -= 2 if f["under_construction"] else 0
    s += min((item.get("area") or 0) / 40, 3)
    if (item.get("rooms") or 0) and (item.get("area") or 0) / item["rooms"] >= 30:
        s += 1  # великі кімнати — типово для старого фонду
    s += 0.5 if len(item.get("photos") or []) >= 3 else 0
    return round(s, 2)


def passes(item: dict, cfg: dict) -> bool:
    rooms, price, area = item.get("rooms"), item.get("price_usd"), item.get("area")
    if rooms is not None and not (cfg["rooms_min"] <= rooms <= cfg["rooms_max"]):
        return False
    if price is None or price > cfg["max_price_usd"] or price < 5000:
        return False
    if area is not None and area < cfg["min_area_m2"]:
        return False
    return True


def base_item(source: str, rid: str, city: dict, **kw) -> dict:
    item = {"id": rid, "source": source, "city": city["key"], "photos": [], "description": "", "phones": []}
    item.update({k: v for k, v in kw.items() if v not in (None, "")})
    return item


# ================================================================ OLX (через API)

OLX_ROOMS = {"odnokomnatnye": 1, "dvuhkomnatnye": 2, "trehkomnatnye": 3, "chetyrehkomnatnye": 4}


def olx_resolve_city(city: dict) -> dict | None:
    oc = city.get("olx") or {}
    if oc.get("city_id"):
        return oc
    data = get("https://www.olx.ua/api/v1/offers/", params={"category_id": 1758, "query": city["name"], "limit": 50}, as_json=True)
    for ad in (data or {}).get("data", []):
        loc = ad.get("location") or {}
        if (loc.get("city") or {}).get("name", "").lower() == city["name"].lower():
            oc = {"city_id": loc["city"]["id"], "region_id": (loc.get("region") or {}).get("id")}
            log(f"  OLX: місто {city['name']} → city_id={oc['city_id']}, region_id={oc['region_id']}")
            return oc
    log(f"  ! OLX: не знайшов city_id для {city['name']}")
    return None


def olx_param(params: list[dict], key: str) -> Any:
    for p in params or []:
        if p.get("key") == key:
            v = p.get("value")
            return v
    return None


def olx_item(ad: dict, rate: float, city: dict) -> dict:
    params = ad.get("params") or []
    price = olx_param(params, "price") or {}
    rooms_v = olx_param(params, "number_of_rooms_string") or {}
    rooms = OLX_ROOMS.get(rooms_v.get("key")) or (int(num(rooms_v.get("label")) or 0) or None)
    label = lambda k: (olx_param(params, k) or {}).get("label") if isinstance(olx_param(params, k), dict) else olx_param(params, k)  # noqa: E731
    extra = []
    for p in params:
        k = p.get("key", "")
        if any(w in k for w in ("type", "wall", "material", "year", "house", "build")) and isinstance(p.get("value"), dict):
            extra.append(p["value"].get("label"))
    m = ad.get("map") or {}
    loc = ad.get("location") or {}
    return base_item(
        "OLX", f"olx-{ad.get('id')}", city,
        url=ad.get("url"),
        title=ad.get("title"),
        price_usd=to_usd(num(price.get("value")), price.get("currency", "UAH"), rate),
        price_orig=price.get("label"),
        rooms=rooms,
        area=num(label("total_area")),
        floor=int(num(label("floor")) or 0) or None,
        floors=int(num(label("total_floors")) or 0) or None,
        building=", ".join(x for x in extra if x),
        condition=label("repair") or "",
        district=(loc.get("district") or {}).get("name"),
        address=city["name"],
        photos=[p["link"].replace("{width}", "1200").replace("{height}", "900") for p in (ad.get("photos") or [])[:20] if p.get("link")],
        seller=(ad.get("user") or {}).get("name") or (ad.get("contact") or {}).get("name"),
        description=clean(ad.get("description")),
        published=(ad.get("created_time") or "")[:10],
        is_owner=not ad.get("business") if ad.get("business") is not None else None,
        lat=m.get("lat"), lon=m.get("lon"), geo_approx=bool(m.get("radius")) or not m.get("show_detailed", True),
    )


def olx_state_item(ad: dict, rate: float, city: dict) -> dict:
    """Оголошення зі стану мобільної сторінки OLX (запасний шлях, якщо API відмовляє)."""
    params = {p.get("key"): p for p in ad.get("params") or []}
    pv = lambda k: (params.get(k) or {}).get("value")  # noqa: E731
    reg = (ad.get("price") or {}).get("regularPrice") or {}
    rooms_raw = (params.get("number_of_rooms_string") or {})
    rooms = OLX_ROOMS.get(rooms_raw.get("normalizedValue")) or (int(num(rooms_raw.get("value")) or 0) or None)
    extra = [pv(k) for k in params if any(w in k for w in ("type", "wall", "material", "year", "house", "build"))]
    loc, m = ad.get("location") or {}, ad.get("map") or {}
    return base_item(
        "OLX", f"olx-{ad.get('id')}", city,
        url=ad.get("url"), title=ad.get("title"),
        price_usd=to_usd(num(reg.get("value")), reg.get("currencyCode", "UAH"), rate),
        price_orig=(ad.get("price") or {}).get("displayValue"),
        rooms=rooms, area=num(pv("total_area")),
        floor=int(num(pv("floor")) or 0) or None, floors=int(num(pv("total_floors")) or 0) or None,
        building=", ".join(str(x) for x in extra if x), condition=pv("repair") or "",
        district=loc.get("districtName"), address=city["name"],
        photos=[re.sub(r"s=\d+x\d+", "s=1200x900", p) for p in (ad.get("photos") or [])[:20] if isinstance(p, str)],
        seller=(ad.get("user") or {}).get("name") or (ad.get("contact") or {}).get("name"),
        description=clean(ad.get("description")), published=(ad.get("createdTime") or "")[:10],
        is_owner=not ad.get("isBusiness") if ad.get("isBusiness") is not None else None,
        lat=m.get("lat"), lon=m.get("lon"), geo_approx=bool(m.get("radius")) or not m.get("show_detailed", True),
    )


def scrape_olx_mobile(cfg: dict, rate: float, city: dict) -> list[dict]:
    slug = (city.get("olx") or {}).get("slug")
    if not slug:
        raise RuntimeError("OLX API відмовив, а адреси міста для мобільної версії немає (olx.slug)")
    items: dict[str, dict] = {}
    for rooms_filter in cfg["olx"].get("mobile_room_filters", [[]]):
      for page in range(1, cfg["olx"].get("mobile_pages", 6) + 1):
        url = f"https://m.olx.ua/uk/nedvizhimost/kvartiry/prodazha-kvartir/{slug}/"
        params = {"page": page, "search[order]": "created_at:desc"}
        for i, r in enumerate(rooms_filter):
            params[f"search[filter_enum_number_of_rooms_string][{i}]"] = r
        html = get(url, params=params)
        if not html:
            if page == 1 and not items:
                raise RuntimeError("OLX не відповідає (ні API, ні мобільна версія)")
            break
        m = re.search(r'window\.__PRERENDERED_STATE__\s*=\s*(".*?");\s*\n', html, re.S) or \
            re.search(r'window\.__PRERENDERED_STATE__\s*=\s*(".*?");', html, re.S)
        if not m:
            break
        state = json.loads(json.loads(m.group(1)))
        ads = (((state.get("listing") or {}).get("listing") or {}).get("ads")) or []
        got = [olx_state_item(ad, rate, city) for ad in ads]
        for g in got:
            items.setdefault(g["id"], g)
        log(f"  OLX (моб.) {city['name']} {'+'.join(rooms_filter) or 'усі'} стор. {page}: {len(got)}, підходять {sum(passes(x, cfg) for x in got)}")
        if not ads:
            break
        time.sleep(2)
    return list(items.values())


def scrape_olx(cfg: dict, rate: float, city: dict) -> list[dict]:
    """Кілька проходів API: спершу з фільтром кімнат (2–4), потім загальний."""
    oc = olx_resolve_city(city)
    if not oc:
        return scrape_olx_mobile(cfg, rate, city)
    items: dict[str, dict] = {}
    per = 50
    for pas in cfg["olx"].get("passes", [{"rooms": [], "pages": 10}]):
        rooms_f = pas.get("rooms") or []
        label = "+".join(str(OLX_ROOMS.get(r, r)) for r in rooms_f) + "к" if rooms_f else "усі"
        for page in range(pas.get("pages", 10)):
            params = {"offset": page * per, "limit": per, "category_id": 1758, "city_id": oc["city_id"],
                      "sort_by": "created_at:desc"}
            if oc.get("region_id"):
                params["region_id"] = oc["region_id"]
            for i, r in enumerate(rooms_f):
                params[f"filter_enum_number_of_rooms_string[{i}]"] = r
            data = get("https://www.olx.ua/api/v1/offers/", params=params, as_json=True)
            if data is None and page == 0 and not items:
                log("  OLX API відмовив — беру мобільну версію сайту")
                return scrape_olx_mobile(cfg, rate, city)
            ads = (data or {}).get("data") or []
            got = [olx_item(ad, rate, city) for ad in ads]
            for g in got:
                items.setdefault(g["id"], g)
            log(f"  OLX {city['name']} {label} стор. {page + 1}: {len(got)}, підходять {sum(passes(x, cfg) for x in got)}")
            if len(ads) < per:
                break
            time.sleep(1.5)
    return list(items.values())


# ================================================================ DOM.RIA

RIA_LINK_RE = re.compile(r"/uk/realty-prodaja-kvartira-[\w-]+-(\d+)\.html")
RIA_PHOTO_RE = re.compile(r"realty__(\d+)|(photos/dom/photo/\d+/\d+/(\d+)/\3)")
RIA_PHOTO_NEW_RE = re.compile(r"photos?(?:new)?/dom/photo/([a-z0-9-]+)__(\d+)(?:f|fl|xl|m|s|b)?\.(?:jpg|jpeg|webp)")
GEO_RE = re.compile(r"[\"']?(?:lat|latitude)[\"']?\s*[:=]\s*[\"']?(4[4-9]\.\d{3,})[\s\S]{0,80}?[\"']?(?:lng|lon|longitude)[\"']?\s*[:=]\s*[\"']?(2[2-9]\.\d{3,}|3[0-9]\.\d{3,})")


def ria_photos_from_html(html: str, slug: str | None = None) -> list[str]:
    seen, out = set(), []
    for m in RIA_PHOTO_NEW_RE.finditer(html):
        s, pid = m.group(1), m.group(2)
        if slug and s != slug or pid in seen:
            continue
        seen.add(pid)
        out.append(f"https://cdn.riastatic.com/photosnew/dom/photo/{s}__{pid}xl.jpg")
    if slug and out:
        return out[:20]
    for m in RIA_PHOTO_RE.finditer(html):
        pid = m.group(1) or m.group(3)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(f"https://cdn.riastatic.com/photosnewr/dom/photo/realty__{pid}-1850x1040x90.webp" if m.group(1)
                   else f"https://cdn.riastatic.com/{m.group(2)}xl.webp")
    return out[:20]


def ria_slug(url: str) -> str | None:
    m = re.search(r"/realty-(prodaja-kvartira-[a-z0-9-]+?)-\d+\.html", url or "")
    return m.group(1) if m else None


def street_query(title: str) -> str | None:
    """'вул. Антонича, 22 Санта Барбара Львів' → 'вул. Антонича, 22'."""
    m = re.match(r"\s*((?:вул\.|просп\.|пр\.|пл\.|бульв\.|пров\.)?\s*[^,]{3,60},\s*\d+[\w/-]*)", title or "")
    return m.group(1).strip() if m else None


def scrape_domria(cfg: dict, rate: float, city: dict) -> list[dict]:
    rc = city.get("domria") or {}
    if not rc.get("slug"):
        return []
    items: dict[str, dict] = {}
    for tpl in cfg["domria"]["url_templates"]:
        base = tpl.format(slug=rc["slug"])
        for page in range(1, cfg["domria"].get("html_pages", 3) + 1):
            html = get(base, params={"page": page} if page > 1 else None)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            found = 0
            for a in soup.find_all("a", href=RIA_LINK_RE):
                m = RIA_LINK_RE.search(a["href"])
                rid = f"ria-{m.group(1)}"
                if rid in items:
                    continue
                card = a
                for _ in range(8):
                    card = card.parent
                    if card is None or "$" in card.get_text():
                        break
                if card is None:
                    continue
                text = card.get_text(" ", strip=True)
                price = num((re.search(r"([\d\s ]{4,})\s*\$", text) or [None, None])[1])
                rooms = num((re.search(r"(\d)\s*кімнат", text) or [None, None])[1])
                area = num((re.search(r"(\d+[.,]?\d*)\s*м²", text) or [None, None])[1])
                fl = re.search(r"(\d+)\s*поверх\s*з\s*(\d+)", text)
                title = a.get("title") or a.get_text(" ", strip=True)[:100]
                items[rid] = base_item(
                    "DOM.RIA", rid, city,
                    url="https://dom.ria.com" + m.group(0),
                    title=title,
                    price_usd=round(price) if price else None,
                    rooms=int(rooms) if rooms else None,
                    area=area,
                    floor=int(fl.group(1)) if fl else None,
                    floors=int(fl.group(2)) if fl else None,
                    photos=ria_photos_from_html(str(card), ria_slug(m.group(0)))[:6],
                    description=text[:1200],
                    address=street_query(title),
                    is_owner=True if "bez-rieltora" in base else None,
                    published=parse_date((re.search(r"(?:Опубликовано|Опубліковано)\s+([^·]+?)(?:\s*·|$)", text) or [None, None])[1])
                              or parse_date((re.search(r"(\d+\s*(?:часа?|часов|годин\w*)\s*(?:назад|тому)|Вчера[^·]*|Вчора[^·]*)", text) or [None, None])[1]),
                    verified=bool(re.search(r"Перевірена квартира|Перевірено", text)),
                )
                found += 1
            log(f"  DOM.RIA {base} стор. {page}: {found}")
            if not found:
                break
            time.sleep(1.5)
    out = list(items.values())
    key = os.environ.get("DOMRIA_API_KEY", "").strip()
    if not out and key:
        log("  DOM.RIA: сторінки не віддали оголошень — беру офіційне API")
        return scrape_domria_api(cfg, rate, city, key)
    enrich(out, cfg, "DOM.RIA", ria_detail, cfg["domria"].get("enrich_per_run", 50))
    return out


def scrape_domria_api(cfg: dict, rate: float, city: dict, key: str) -> list[dict]:
    """Запасний шлях через developers.ria.com. Ощадливо щодо ліміту (1000 запитів/міс.):
    кілька пошукових запитів і деталі лише для оголошень, яких ще немає в базі."""
    rc = city.get("domria") or {}
    if not rc.get("state_id") or not rc.get("city_id"):
        log(f"  ! DOM.RIA API: для {city['name']} не вказано state_id/city_id")
        return []
    ids: list[int] = []
    used = 0
    for page in range(cfg["domria"].get("api_pages", 2)):
        data = get("https://developers.ria.com/dom/search", as_json=True, tries=1, params={
            "api_key": key, "category": 1, "realty_type": 2, "operation_type": 1,
            "state_id": rc["state_id"], "city_id": rc["city_id"], "page": page, "sort": "created_at"})
        used += 1
        if not data or not data.get("items"):
            break
        ids.extend(data["items"])
    out, skipped = [], 0
    for rid in dict.fromkeys(ids):
        if f"ria-{rid}" in KNOWN:
            out.append(dict(KNOWN[f"ria-{rid}"]))
            continue
        if used >= cfg["domria"].get("api_requests_per_run", 25):
            skipped += 1
            continue
        info = get(f"https://developers.ria.com/dom/info/{rid}", params={"api_key": key, "lang_id": 4}, as_json=True, tries=1)
        used += 1
        if not info:
            continue
        price = num((info.get("priceArr") or {}).get("1"))
        url = info.get("beautiful_url") or ""
        url = url if url.startswith("http") else "https://dom.ria.com/uk/" + url.lstrip("/")
        street = " ".join(str(x) for x in (info.get("street_name_uk") or info.get("street_name"), info.get("building_number_str")) if x)
        out.append(base_item(
            "DOM.RIA", f"ria-{rid}", city, url=url, title=street or "Квартира",
            price_usd=round(price) if price else None,
            rooms=info.get("rooms_count"), area=num(info.get("total_square_meters")),
            floor=info.get("floor"), floors=info.get("floors_count"),
            year_built=int(num(info.get("year_of_construction")) or 0) or None,
            district=info.get("district_name_uk") or info.get("district_name"),
            address=f"{street}, {city['name']}" if street else None,
            description=clean(info.get("description_uk") or info.get("description")),
            published=(info.get("publishing_date") or "")[:10] or None,
            lat=num(info.get("latitude")), lon=num(info.get("longitude")),
        ))
        time.sleep(0.4)
    log(f"  DOM.RIA API: запитів {used}" + (f", відкладено {skipped}" if skipped else ""))
    return out


def ria_detail(it: dict, html: str) -> None:
    photos = ria_photos_from_html(html, ria_slug(it["url"]))
    if len(photos) > len(it.get("photos") or []):
        it["photos"] = photos
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one("#descriptionBlock, .descriptions-ticket, [class*=description] .boxed")
    if desc:
        text = desc.get_text("\n", strip=True)
        if len(text) > len(it.get("description") or ""):
            it["description"] = text[:2000]
    g = GEO_RE.search(html)
    if g:
        it["lat"], it["lon"] = float(g.group(1)), float(g.group(2))


# ================================================================ новобудови від забудовників (каталог DOM.RIA)

def ria_init(html: str) -> dict | None:
    m = re.search(r"<script>window\.init=(\{.*?\});?</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(re.sub(r'(?<=[:\[,])undefined(?=[,\]}])', "null", m.group(1)))
    except Exception as e:  # noqa: BLE001
        log("  ! DOM.RIA: не вдалося розібрати дані сторінки:", e)
        return None


def complex_details(url: str, nid: int, cfg: dict):
    """Сторінка ЖК (фото) і сторінки планувань по кімнатності: плани з зображеннями, поверхами й цінами."""
    photos, floors_max = [], None
    d = ria_init(get(url) or "") or {}
    for p in sorted((d.get("newbuildPhotos") or {}).values(), key=lambda x: x.get("priority") or 99):
        if p.get("newbuildId") == nid and p.get("type") in ("new-gallery", "new-master-plan") and p.get("img_620x460"):
            photos.append(p["img_620x460"])
    time.sleep(1)
    plans, by = [], {}
    for r in range(cfg["rooms_min"], 5):
        html = get(url.rstrip("/") + f"/planirovki/{r}k/")
        d = ria_init(html or "") or {}
        secs = d.get("sections") or {}
        for p in (d.get("plans") or {}).values():
            if p.get("newbuildId") != nid or p.get("sold") or not p.get("fullAreaPriceUsd") or (p.get("roomCount") or 0) < r:
                continue
            if r < 4 and p.get("roomCount") != r:
                continue
            sec = secs.get(str(p.get("sectionId"))) or {}
            if sec.get("floors"):
                floors_max = max(floors_max or 0, int(sec["floors"]))
            rel = sec.get("release") or {}
            plans.append({
                "id": p.get("planId"), "rooms": p.get("roomCount"), "area": p.get("areaMin"),
                "price_usd": round(p["fullAreaPriceUsd"]), "m2_usd": round(p.get("areaPriceUsd") or 0),
                "floors": p.get("floorsList"), "building_floors": sec.get("floors"),
                "photo": p.get("photo"), "name": p.get("planIdDev"), "count": p.get("flatsCount"),
                "release": f"{rel.get('quarter')} кв. {rel.get('year')}" if rel.get("year") else None,
                "section": sec.get("sectionNumber"),
            })
        time.sleep(1)
    for p in plans:
        k = str(min(p["rooms"], 4))
        b = by.setdefault(k, {"from_usd": 10**12, "area_min": 10**9, "area_max": 0, "plans": 0})
        b["from_usd"] = min(b["from_usd"], p["price_usd"])
        b["area_min"] = min(b["area_min"], p["area"] or 10**9)
        b["area_max"] = max(b["area_max"], p["area"] or 0)
        b["plans"] += 1
    plans.sort(key=lambda p: (p["rooms"], p["price_usd"]))
    keep = cfg.get("newbuilds", {}).get("plans_per_rooms", 12)
    trimmed, cnt = [], {}
    for p in plans:
        cnt[p["rooms"]] = cnt.get(p["rooms"], 0) + 1
        if cnt[p["rooms"]] <= keep:
            trimmed.append(p)
    return dict(sorted(by.items())), trimmed, photos[:15], floors_max, TODAY


def scrape_complexes(cfg: dict, rate: float, city: dict, known: dict) -> list[dict]:
    """Житлові комплекси з квартирами в продажу: ціни «від» за кімнатністю, стан будівництва, розтермінування."""
    rc = city.get("domria") or {}
    nc = cfg.get("newbuilds", {})
    if not rc.get("slug"):
        return []
    found: dict[int, dict] = {}
    urls: dict[int, str] = {}
    developers: dict[int, str] = {}
    for page in range(1, nc.get("catalog_pages", 30) + 1):
        html = get(f"https://dom.ria.com/uk/novostroyki/{rc['slug']}/", params={"page": page} if page > 1 else None)
        d = ria_init(html or "")
        if not d:
            break
        for m in re.finditer(r'href="(?:https://dom\.ria\.com)?(/uk/novostroyka-[\w-]+?-(\d+)/)', html):
            urls.setdefault(int(m.group(2)), "https://dom.ria.com" + m.group(1))
        for dev in (d.get("developers") or {}).values():
            if isinstance(dev, dict) and dev.get("developerId"):
                developers[dev["developerId"]] = dev.get("name_uk") or dev.get("name")
        ids = (d.get("catalog") or {}).get("newbuilds") or []
        if not ids:
            break
        for nid in ids:
            nb = (d.get("newbuilds") or {}).get(str(nid)) or (d.get("newbuilds") or {}).get(nid)
            if nb and (not rc.get("city_id") or nb.get("cityId") in (None, rc["city_id"])):
                found[int(nid)] = dict(nb, _nid=int(nid))
        log(f"  Новобудови {city['name']} стор. {page}: {len(ids)}")
        time.sleep(1)
    max_m2 = cfg["max_price_usd"] / nc.get("min_area_for_prefilter", 45)
    cands = [nb for nb in found.values()
             if (nb.get("withFlats") or (nb.get("availableFlatsCount") or 0) > 0) and 0 < (nb.get("priceUSD") or 0) <= max_m2]
    cands.sort(key=lambda nb: nb.get("priceUSD") or 1e9)
    log(f"  Новобудови: у каталозі {len(found)}, з квартирами в продажу й ціною до ${max_m2:.0f}/м² — {len(cands)}")
    out, opened = [], 0
    for nb in cands:
        nid = nb["_nid"]
        cid = f"zk-{nid}"
        old = known.get(cid)
        rooms, checked, plans, photos, floors_max = None, None, [], [], None
        if old and old.get("rooms_checked") and "plans" in old and (dt.date.today() - dt.date.fromisoformat(old["rooms_checked"])).days < nc.get("recheck_days", 3):
            rooms, checked = old.get("rooms"), old["rooms_checked"]
            plans, photos, floors_max = old.get("plans") or [], old.get("photos") or [], old.get("floors_max")
        elif opened < nc.get("complexes_per_run", 40) and urls.get(nid):
            opened += 1
            rooms, plans, photos, floors_max, checked = complex_details(urls[nid], nid, cfg)
        if rooms is None and old:
            rooms, checked = old.get("rooms"), old.get("rooms_checked")
            plans, photos, floors_max = old.get("plans") or [], old.get("photos") or [], old.get("floors_max")
        rel = nb.get("release") or {}
        ready, notready = nb.get("readySectionsCount") or 0, nb.get("notReadySectionsCount") or 0
        status = "ready" if ready and not notready else "partly" if ready else "building"
        promo = clean(nb.get("promoText") or "", 600)
        out.append({
            "id": cid, "kind": "complex", "source": "DOM.RIA", "city": city["key"],
            "name": nb.get("name") or nb.get("name2"), "developer": developers.get(nb.get("developerId")),
            "street": nb.get("streetName") or nb.get("street"),
            "url": urls.get(nid) or f"https://dom.ria.com/uk/novostroyki/{rc['slug']}/",
            "photo": (nb.get("img_620x460") or nb.get("img") or "").replace("-276x183", "-620x460"),
            "floors_min_cat": nb.get("floorMin"), "floors_max_cat": nb.get("floorMax"),
            "lat": nb.get("latitude"), "lon": nb.get("longitude"),
            "price_m2_usd": nb.get("priceUSD"), "area_min": nb.get("areaMin"), "area_max": nb.get("areaMax"),
            "available": nb.get("availableFlatsCount"),
            "status": status, "ready_sections": ready, "building_sections": notready,
            "release": f"{rel.get('quarter')} кв. {rel.get('year')}" if rel.get("year") else None,
            "installment": {"months": nb.get("installmentPeriodMax"), "first_pct": nb.get("installmentFirstPaymentMin")}
                           if nb.get("paymentByInstallments") else None,
            "credit": bool(nb.get("hasCredit") or nb.get("banksWithCredit")),
            "voucher": bool(nb.get("housingVoucher")),
            "verified": bool(nb.get("inspected")),
            "promo": nb.get("promoShort"), "promo_text": promo,
            "rooms": rooms, "rooms_checked": checked,
            "plans": plans, "photos": photos, "floors_max": floors_max,
            "first_seen": (old or {}).get("first_seen", TODAY), "last_seen": TODAY,
        })
    log(f"  Новобудови: відкрито сторінок ЖК {opened}")
    return out


# ================================================================ rieltor.ua

def scrape_rieltor(cfg: dict, rate: float, city: dict) -> list[dict]:
    slug = city.get("rieltor")
    if not slug:
        return []
    items: dict[str, dict] = {}
    for rooms in cfg["rieltor"].get("rooms", [1, 2, 3, 4]):
        for page in range(1, cfg["rieltor"].get("pages", 3) + 1):
            html = get(f"https://rieltor.ua/{slug}/flats-sale/", params={"rooms": rooms, "page": page} if page > 1 else {"rooms": rooms})
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".catalog-card")
            found = 0
            for c in cards:
                a = c.find("a", href=re.compile(r"/flats-sale/view/\d+/"))
                if not a:
                    continue
                rid = "rieltor-" + re.search(r"/view/(\d+)/", a["href"]).group(1)
                if rid in items:
                    continue
                t = lambda sel: (c.select_one(sel).get_text(" ", strip=True) if c.select_one(sel) else "")  # noqa: E731
                details = t(".catalog-card-details")
                area = num((re.search(r"(\d+[.,]?\d*)\s*/", details) or re.search(r"(\d+[.,]?\d*)\s*м²", details) or [None, None])[1])
                fl = re.search(r"поверх\s*(\d+)\s*з\s*(\d+)", details)
                year = re.search(r"(\d{4})\s*рік", details)
                rooms_n = num((re.search(r"(\d+)\s*кімнат", details) or [None, None])[1])
                region = t(".catalog-card-region")
                photos = []
                for img in c.find_all("img"):
                    src = img.get("src") or img.get("data-src") or ""
                    if "/images/offers/" in src:
                        u = re.sub(r"/lun-ua/\d+/\d+/", "/lun-ua/1140/721/", src)
                        if u not in photos:
                            photos.append(u)
                author = t(".catalog-card-author-subtitle")
                updated = parse_date(t(".catalog-card-update"))
                phones = [p.get_text(strip=True) for p in c.select(".catalog-card-author-phones")]
                url = a["href"] if a["href"].startswith("http") else "https://rieltor.ua" + a["href"]
                address = t(".catalog-card-address")
                items[rid] = base_item(
                    "rieltor.ua", rid, city,
                    url=url.split("?")[0],
                    title=address or "Квартира",
                    price_usd=money(t(".catalog-card-price-title"), rate),
                    price_orig=t(".catalog-card-price-title"),
                    rooms=int(rooms_n) if rooms_n else rooms,
                    area=area,
                    floor=int(fl.group(1)) if fl else None,
                    floors=int(fl.group(2)) if fl else None,
                    year_built=int(year.group(1)) if year else None,
                    building=f"{year.group(1)} рік" if year else "",
                    district=", ".join(x.strip() for x in region.split(",")[1:] if x.strip()) or t(".catalog-card-chips"),
                    address=f"{address}, {city['name']}" if address else None,
                    photos=photos,
                    description=clean(t(".catalog-card-description") or ""),
                    seller=" · ".join(x for x in (t(".catalog-card-author-title"), author) if x),
                    phones=list(dict.fromkeys(phones)),
                    is_owner=("власник" in author.lower()) if author else None,
                    updated=updated,
                )
                found += 1
            log(f"  rieltor {city['name']} {rooms}к стор. {page}: {found}")
            if not found or len(cards) < 10:
                break
            time.sleep(1.5)
    out = list(items.values())
    enrich(out, cfg, "rieltor.ua", rieltor_detail, cfg["rieltor"].get("enrich_per_run", 40), cfg["rieltor"].get("detail_pause", 5))
    return out


def rieltor_detail(it: dict, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    photos = []
    for u in re.findall(r"https://market-images\.lunstatic\.net/lun-ua/\d+/\d+/images/offers/(\d+)\.jpg", html):
        full = f"https://market-images.lunstatic.net/lun-ua/1140/721/images/offers/{u}.jpg"
        if full not in photos:
            photos.append(full)
    if len(photos) > len(it.get("photos") or []):
        it["photos"] = photos[:20]
    for sel in (".offer-view-section-text", ".offer-description", "[class*=description]"):
        d = soup.select_one(sel)
        if d and len(d.get_text(strip=True)) > len(it.get("description") or ""):
            it["description"] = d.get_text("\n", strip=True)[:2000]
            break
    g = GEO_RE.search(html)
    if g:
        it["lat"], it["lon"] = float(g.group(1)), float(g.group(2))


# ================================================================ mistechko.com

def scrape_mistechko(cfg: dict, rate: float, city: dict) -> list[dict]:
    slug = city.get("mistechko")
    if not slug:
        return []
    items: dict[str, dict] = {}
    for tpl in cfg["mistechko"]["url_templates"]:
        for page in range(1, cfg["mistechko"].get("pages", 3) + 1):
            url = tpl.format(slug=slug, page=page)
            if page > 1 and "{page}" not in tpl:
                break
            html = get(url)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            found = 0
            for c in soup.select(".sr-2-list-item-n-in"):
                a = c.find("a", href=re.compile(r"/prodazh-kvartir/[\w-]+-\d+\.html"))
                if not a:
                    continue
                href = a["href"] if a["href"].startswith("http") else "https://mistechko.com" + a["href"]
                rid = "mistechko-" + re.search(r"-(\d+)\.html", href).group(1)
                if rid in items:
                    continue
                t = lambda sel: (c.select_one(sel).get_text(" ", strip=True) if c.select_one(sel) else "")  # noqa: E731
                desc = t(".sr-2-item-desc")  # «3-кімн , 2/3 , 75 м² , Австрійський»
                parts = [p.strip() for p in desc.split(",")]
                rooms = num((re.search(r"(\d+)\s*-?\s*кімн", desc) or [None, None])[1])
                fl = re.search(r"(\d+)\s*/\s*(\d+)", desc)
                area = num((re.search(r"(\d+[.,]?\d*)\s*м²", desc) or [None, None])[1])
                btype = next((p for p in parts if p and not re.search(r"\d", p)), "")
                photos = []
                for img in c.find_all("img"):
                    src = img.get("data-src") or img.get("src") or ""
                    if "/files/images/items/" in src:
                        u = re.sub(r"/(\d+)[a-z]([0-9a-f]{8})\.jpg", r"/\1v\2.jpg", src)
                        u = u if u.startswith("http") else "https://mistechko.com" + u
                        if u not in photos:
                            photos.append(u)
                addr = t(".sr-2-item-address")
                mdate = parse_date(t(".sr-2-item-date"))
                items[rid] = base_item(
                    "mistechko", rid, city,
                    url=href,
                    title=t(".sr-2-list-item-n-title") or "Квартира",
                    price_usd=money(t(".sr-2-list-item-n-price"), rate),
                    price_orig=t(".sr-2-list-item-n-price"),
                    rooms=int(rooms) if rooms else None,
                    area=area,
                    floor=int(fl.group(1)) if fl else None,
                    floors=int(fl.group(2)) if fl else None,
                    building=btype,
                    district=addr.split(",", 1)[1].strip() if "," in addr else None,
                    photos=photos,
                    updated=mdate,
                )
                found += 1
            log(f"  mistechko {url} : {found}")
            if not found:
                break
            time.sleep(1.5)
    out = list(items.values())
    enrich(out, cfg, "mistechko", mistechko_detail, cfg["mistechko"].get("enrich_per_run", 40))
    return out


def mistechko_detail(it: dict, html: str) -> None:
    mid = it["id"].split("-")[1]
    photos = []
    for m in re.finditer(rf"(files/images/items/\d+/){mid}[a-z]([0-9a-f]{{8}})\.jpg", html):
        full = f"https://mistechko.com/{m.group(1)}{mid}v{m.group(2)}.jpg"
        if full not in photos:
            photos.append(full)
    if len(photos) > len(it.get("photos") or []):
        it["photos"] = photos[:20]
    soup = BeautifulSoup(html, "html.parser")
    for sel in (".j-item-description", ".item-description", "[itemprop=description]", ".v-descr_text", ".description"):
        d = soup.select_one(sel)
        if d and len(d.get_text(strip=True)) > 40:
            it["description"] = d.get_text("\n", strip=True)[:2000]
            break
    g = GEO_RE.search(html)
    if g:
        it["lat"], it["lon"] = float(g.group(1)), float(g.group(2))
    text = it.get("description", "")
    if re.search(r"від власник|власник", text, re.I):
        it["is_owner"] = True


# ================================================================ svitkvartyr.com

def scrape_svitkvartyr(cfg: dict, rate: float, city: dict) -> list[dict]:
    slug = city.get("svitkvartyr")
    if not slug:
        return []
    items: dict[str, dict] = {}
    for tpl in cfg["svitkvartyr"]["url_templates"]:
        for page in range(1, cfg["svitkvartyr"].get("pages", 3) + 1):
            base = tpl.format(slug=slug)
            html = get(base, params={"page": page} if page > 1 else None)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            found = 0
            for c in soup.select(".advert-card"):
                a = c.find("a", href=re.compile(r"/adverts/\d+"))
                if not a:
                    continue
                rid = "svit-" + re.search(r"/adverts/(\d+)", a["href"]).group(1)
                if rid in items:
                    continue
                vals = {}
                for one in c.select(".acd-one"):
                    v = one.select_one(".acd-one-value")
                    lbl = one.select_one(".acd-one-label")
                    if v and lbl:
                        vals[lbl.get_text(" ", strip=True).lower()] = v.get_text(" ", strip=True)
                text = c.get_text(" ", strip=True)
                price_txt = next((f"{v} {k}" for k, v in vals.items() if "грн" in k or "$" in k or "дол" in k), "")
                if not price_txt:
                    pm = re.search(r"([\d\s]{4,})\s*(грн|\$|USD|€)", text)
                    price_txt = pm.group(0) if pm else ""
                rooms = num(next((v for k, v in vals.items() if "кімнат" in k), None))
                area = num(next((v for k, v in vals.items() if "кв" in k), None))
                fl = re.search(r"(\d+)\s*/\s*(\d+)\s*поверх", text)
                material = next((v for k, v in vals.items() if "матеріал" in k), "")
                btype = next((v for k, v in vals.items() if "тип" in k), "")
                title = c.select_one(".title").get_text(" ", strip=True) if c.select_one(".title") else "Квартира"
                dm = re.search(r"тип будівлі\s*(.+)$", text, re.S)
                upd = parse_date((re.search(r"Оновлено:\s*([\d/]+)", text) or [None, None])[1])
                items[rid] = base_item(
                    "svitkvartyr", rid, city,
                    url="https://svitkvartyr.com" + re.search(r"/adverts/\d+", a["href"]).group(0),
                    title=title,
                    price_usd=money(price_txt, rate),
                    price_orig=price_txt,
                    rooms=int(rooms) if rooms else None,
                    area=area,
                    floor=int(fl.group(1)) if fl else None,
                    floors=int(fl.group(2)) if fl else None,
                    building=", ".join(x for x in (btype, material) if x),
                    district=title.split(",", 1)[1].strip() if "," in title else None,
                    address=f"вул. {title.split(',')[0]}, {city['name']}" if "," in title else None,
                    description=clean(dm.group(1)) if dm else "",
                    updated=upd,
                )
                found += 1
            log(f"  svitkvartyr {base} стор. {page}: {found}")
            if not found:
                break
            time.sleep(1.5)
    return list(items.values())


# ================================================================ спільне: сторінка оголошення, геокодування

def enrich(items: list[dict], cfg: dict, name: str, fn: Callable[[dict, str], None], limit: int, pause: float = 1.0) -> None:
    """Для нових підхожих оголошень відкриває сторінку оголошення: всі фото, опис, координати."""
    n = 0
    for it in items:
        if not passes(it, cfg):
            continue
        old = KNOWN.get(it["id"]) or {}
        if len(old.get("photos") or []) >= 4 and old.get("description"):
            continue
        if n >= limit:
            break
        html = get(it["url"])
        n += 1
        if html:
            try:
                fn(it, html)
            except Exception as e:  # noqa: BLE001
                log(f"  ! {name}: не вдалося розібрати {it['url']}: {e}")
        time.sleep(pause)
    log(f"  {name}: відкрито сторінок оголошень {n}")


def geocode(items: list[dict], cfg: dict) -> None:
    """Координати за адресою через OpenStreetMap Nominatim (з кешем, ≤1 запит/с)."""
    cache = json.loads(GEOCACHE_PATH.read_text(encoding="utf-8")) if GEOCACHE_PATH.exists() else {}
    city_names = {c["key"]: c["name"] for c in cfg["cities"]}
    n = 0
    for it in items:
        if it.get("lat") or not passes(it, cfg):
            continue
        addr = (it.get("address") or "").strip()
        if not addr or addr in city_names.values():
            continue
        q = addr if city_names.get(it.get("city"), "") in addr else f"{addr}, {city_names.get(it.get('city'), '')}"
        if q not in cache:
            if n >= cfg.get("geocode_per_run", 60):
                continue
            res = get("https://nominatim.openstreetmap.org/search", as_json=True, tries=1,
                      params={"q": q, "format": "json", "limit": 1, "countrycodes": "ua", "accept-language": "uk"},
                      headers={"User-Agent": "FlatSearcher/1.0 (personal apartment search; github.com/Gaspode3/FlatSearcher)"})
            n += 1
            cache[q] = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
            time.sleep(1.1)
        if cache.get(q):
            it["lat"], it["lon"] = cache[q]
            it["geo_approx"] = True
    GEOCACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEOCACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    log(f"  Геокодування: запитів {n}")


# ================================================================ свіжість і дублікати

PRIMARY = {"OLX", "DOM.RIA", "rieltor.ua"}
TRUST = {"OLX": 0, "DOM.RIA": 0, "rieltor.ua": 0, "mistechko": -10, "svitkvartyr": -30, "Вручну": 0}
SOURCE_RANK = {"OLX": 3, "DOM.RIA": 3, "rieltor.ua": 2, "mistechko": 1, "svitkvartyr": 0, "Вручну": -1}


def street_key(it: dict) -> str:
    text = (it.get("address") or it.get("title") or "").lower()
    text = re.sub(r"\b(вул|вулиця|просп|пр|пл|площа|бульв|пров)\b\.?", " ", text)
    m = re.search(r"[а-яіїєґ'’-]{4,}", text)
    return m.group(0)[:6] if m else ""


def analyze(items: list[dict], cfg: dict) -> None:
    """Дублікати між майданчиками, «агентські бази» за телефоном і оцінка свіжості 0–100."""
    today = dt.date.today()
    # 1) дублікати: однакові кімнати, площа ±1.5 м², ціна ±4 %, та сама вулиця (якщо відома)
    for it in items:
        it.pop("dup_of", None)
        it["also_on"] = []
    live = [i for i in items if i.get("active", True) and i.get("area") and i.get("price_usd")]
    live.sort(key=lambda i: (-SOURCE_RANK.get(i["source"], 0), -len(i.get("photos") or [])))
    for i, a in enumerate(live):
        if a.get("dup_of"):
            continue
        for b in live[i + 1:]:
            if b.get("dup_of") or b["source"] == a["source"] or a.get("rooms") != b.get("rooms"):
                continue
            if abs(a["area"] - b["area"]) > 1.5 or abs(a["price_usd"] - b["price_usd"]) > 0.04 * a["price_usd"]:
                continue
            ka, kb = street_key(a), street_key(b)
            if ka and kb and ka != kb:
                continue
            if not (ka and kb) and a.get("district") and b.get("district") and a["district"].split()[0] != b["district"].split()[0]:
                continue
            b["dup_of"] = a["id"]
            a["also_on"].append({"source": b["source"], "url": b["url"], "id": b["id"]})
            b["also_on"].append({"source": a["source"], "url": a["url"], "id": a["id"]})
            for k in ("phones", "photos"):
                if len(b.get(k) or []) > len(a.get(k) or []) and k == "photos":
                    a[k] = b[k]
            a["phones"] = list(dict.fromkeys((a.get("phones") or []) + (b.get("phones") or [])))
            if not a.get("lat") and b.get("lat"):
                a["lat"], a["lon"], a["geo_approx"] = b["lat"], b["lon"], b.get("geo_approx", True)
    # 2) скільки оголошень на одному телефоні
    per_phone: dict[str, int] = {}
    for it in items:
        for p in set(re.sub(r"\D", "", x)[-10:] for x in it.get("phones") or []):
            per_phone[p] = per_phone.get(p, 0) + 1
    # 3) оцінка свіжості
    for it in items:
        if it["source"] == "Вручну":
            it["fresh"], it["fresh_why"] = None, []
            continue
        sc, why = 70, []
        posted = it.get("published") or it.get("updated")
        if it.get("published"):
            age = (today - dt.date.fromisoformat(it["published"])).days
            if age > 90:
                sc -= 40; why.append(f"опубліковано {age} дн. тому")
            elif age > 45:
                sc -= 15; why.append(f"опубліковано {age} дн. тому")
            elif age <= 14:
                sc += 10
        elif posted and it["source"] != "svitkvartyr":
            if (today - dt.date.fromisoformat(posted)).days <= 14:
                sc += 5
        seen_days = (today - dt.date.fromisoformat(it.get("first_seen", today.isoformat()))).days
        if seen_days > 60:
            sc -= 10; why.append(f"висить у нас уже {seen_days} дн.")
        t = TRUST.get(it["source"], 0)
        if t:
            sc += t; why.append(f"джерело з нижчою довірою ({it['source']})")
        if any(x["source"] in PRIMARY for x in it.get("also_on") or []):
            sc += 20
        elif it["source"] not in PRIMARY:
            why.append("немає на OLX / DOM.RIA / rieltor")
        if it.get("verified"):
            sc += 30
        addr = it.get("address") or it.get("title") or ""
        if it["source"] in ("svitkvartyr", "mistechko") and not re.search(r"\d", addr):
            sc -= 10; why.append("без номера будинку")
        busy = max((per_phone.get(re.sub(r"\D", "", p)[-10:], 0) for p in it.get("phones") or []), default=0)
        if busy >= cfg.get("agency_phone_threshold", 10):
            sc -= 15; why.append(f"телефон у {busy} оголошеннях — схоже на агентську базу")
        it["fresh"] = max(0, min(100, sc))
        it["fresh_why"] = why
        it["suspicious"] = it["fresh"] < cfg.get("fresh_threshold", 50)
        it["score"] = round(score(it) - (2 if it["suspicious"] else 0), 2)


# ================================================================ merge & save

SOURCES: dict[str, tuple[str, Callable]] = {
    "olx": ("OLX", scrape_olx),
    "domria": ("DOM.RIA", scrape_domria),
    "rieltor": ("rieltor.ua", scrape_rieltor),
    "mistechko": ("mistechko", scrape_mistechko),
    "svitkvartyr": ("svitkvartyr", scrape_svitkvartyr),
}


def load_existing() -> dict:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {"items": []}


def main() -> int:
    global DEBUG, CFG, KNOWN, DATA_PATH, NEW_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--sources", help="через кому: " + ", ".join(SOURCES) + "; за замовчуванням — default_sources з config.json")
    ap.add_argument("--out", default="docs/data/listings.json", help="файл даних для сайту")
    ap.add_argument("--new", default="new_listings.md", help="файл зі списком нових варіантів")
    ap.add_argument("--geocache", default="docs/data/geocache.json", help="кеш координат")
    ap.add_argument("--home", action="store_true", help="запуск з домашнього комп'ютера: home_sources → listings-home.json")
    args = ap.parse_args()
    DEBUG = args.debug
    global GEOCACHE_PATH
    if args.home and not json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("home_enabled", True):
        log("Домашній збір вимкнено (home_enabled: false у config.json) — усе збирає GitHub. Нічого не роблю.")
        return 0
    if args.home:
        args.out, args.new, args.geocache = "docs/data/listings-home.json", "notify/home-new.md", "docs/data/geocache-home.json"
    DATA_PATH = ROOT / args.out
    GEOCACHE_PATH = ROOT / args.geocache
    NEW_PATH = ROOT / args.new
    CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    existing = load_existing()
    KNOWN = {it["id"]: it for it in existing.get("items", [])}
    rate = usd_rate()
    log(f"Курс USD: {rate:.2f}")

    scraped: list[dict] = []
    errors: list[str] = []
    ok_sources: set[tuple[str, str]] = set()
    default = CFG["home_sources"] if args.home else CFG["default_sources"]
    wanted = [w.strip() for w in (args.sources or ",".join(default)).split(",") if w.strip()]
    cities = [c for c in CFG["cities"] if c.get("enabled")]
    for city in cities:
        for key in wanted:
            if key not in SOURCES:
                if key != "newbuilds":
                    log(f"  ! невідоме джерело {key}")
                continue
            name, fn = SOURCES[key]
            log(f"→ {name} · {city['name']}")
            try:
                got = fn(CFG, rate, city)
                log(f"  всього з {name}: {len(got)}, підходять {sum(passes(x, CFG) for x in got)}")
                scraped.extend(got)
                if got:
                    ok_sources.add((name, city["key"]))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name} ({city['name']}): {e}")
                log(f"  ! {name} впав: {e}")

    complexes = [c for c in existing.get("complexes", [])]
    if "newbuilds" in wanted:
        known_c = {c["id"]: c for c in complexes}
        fresh_c = []
        for city in cities:
            log(f"→ Новобудови (DOM.RIA) · {city['name']}")
            try:
                fresh_c += scrape_complexes(CFG, rate, city, known_c)
            except Exception as e:  # noqa: BLE001
                errors.append(f"Новобудови ({city['name']}): {e}")
                log(f"  ! Новобудови впали: {e}")
        if fresh_c:
            complexes = fresh_c
    try:
        geocode(scraped, CFG)
    except Exception as e:  # noqa: BLE001
        log("  ! геокодування:", e)

    by_id = {it["id"]: it for it in existing.get("items", [])}
    # Оголошення OLX і rieltor «належать» домашньому ПК, щойно з'явиться його файл даних
    home_file = ROOT / "docs" / "data" / "listings-home.json"
    if not args.home and home_file.exists():
        home_names = {SOURCES[k][0] for k in CFG.get("home_sources", []) if k in SOURCES}
        by_id = {k: v for k, v in by_id.items() if v.get("source") not in home_names}
    new_items = []
    for it in scraped:
        if not it.get("id") or not it.get("url") or not passes(it, CFG):
            continue
        it["flags"] = make_flags(it, CFG)
        it["phones"] = list(dict.fromkeys((it.get("phones") or []) + phones_from_text(it.get("description", ""))))
        old = by_id.get(it["id"])
        if old:
            it["first_seen"] = old.get("first_seen", TODAY)
            hist = old.get("price_history") or []
            if it.get("price_usd") and (not hist or hist[-1]["usd"] != it["price_usd"]):
                hist.append({"date": TODAY, "usd": it["price_usd"]})
            it["price_history"] = hist
            for k, v in old.items():  # не губимо дані, яких цього разу не вдалося зібрати
                if k not in it or it[k] in (None, "", []):
                    it[k] = v
                elif k == "photos" and len(v or []) > len(it[k]):
                    it[k] = v
        else:
            it["first_seen"] = TODAY
            it["price_history"] = [{"date": TODAY, "usd": it["price_usd"]}]
            new_items.append(it)
        it["score"] = score(it)
        it["last_seen"] = TODAY
        it["active"] = True
        by_id[it["id"]] = it

    # Оголошення, якого сьогодні не було, позначаємо зниклим — лише якщо його джерело
    # для цього міста сьогодні спрацювало (інакше це збій збору, а не продаж).
    cutoff = (dt.date.today() - dt.timedelta(days=CFG["keep_inactive_days"])).isoformat()
    src_name = {"olx": "OLX", "ria": "DOM.RIA", "rieltor": "rieltor.ua", "mistechko": "mistechko", "svit": "svitkvartyr"}
    for it in by_id.values():
        if it.get("source") == "Вручну" or it.get("last_seen", TODAY) >= TODAY:
            continue
        if (src_name.get(it["id"].split("-")[0]), it.get("city", "lviv")) in ok_sources:
            it["active"] = False
    items = [it for it in by_id.values() if it.get("active") or it.get("last_seen", TODAY) >= cutoff or it.get("source") == "Вручну"]
    # дублікати шукаємо й серед оголошень з інших файлів даних (домашній ПК ↔ GitHub)
    others = []
    for f in CFG.get("data_files", []):
        p = ROOT / f
        if p.resolve() != DATA_PATH.resolve() and p.exists():
            own = {it["id"] for it in items}
            others += [it for it in json.loads(p.read_text(encoding="utf-8")).get("items", []) if it["id"] not in own]
    analyze(items + others, CFG)
    items.sort(key=lambda x: (-x.get("score", 0), x.get("price_usd") or 0))

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "usd_rate": round(rate, 2),
        "criteria": {k: CFG[k] for k in ("rooms_min", "rooms_max", "budget_usd", "max_price_usd", "default_rooms")},
        "cities": [{"key": c["key"], "name": c["name"]} for c in cities],
        "complexes": complexes,
        "errors": errors,
        "items": items,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    NEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    def worth(it: dict) -> bool:
        r, a = it.get("rooms") or 0, it.get("area") or 0
        return (r >= CFG["notify_rooms_min"] or (r >= 2 and a >= CFG.get("notify_min_area_2rooms", 60))) \
            and not it["flags"]["over_budget"] and not it.get("dup_of")
    notify = [it for it in new_items if worth(it)]
    if notify:
        notify.sort(key=lambda x: -x["score"])
        site = CFG.get("site_url", "").rstrip("/")
        lines = [f"Нових варіантів у бюджеті ({CFG['notify_rooms_min']}+ кімнати або 2 кімнати від {CFG.get('notify_min_area_2rooms', 60)} м²): **{len(notify)}**\n"]
        for it in notify[:30]:
            tags = [t for t, on in (("🏛 старий фонд", it["flags"]["old_fund"]), ("🔨 під ремонт", it["flags"]["needs_repair"]),
                                    ("👤 власник", it["flags"]["owner"])) if on]
            link = f"{site}/#id={it['id']}" if site else it["url"]
            lines.append(
                f"- **${it['price_usd']:,}** · {it.get('rooms') or '?'} кімн. · {it.get('area') or '?'} м² · "
                f"{it.get('district') or ''} — [{it.get('title') or 'оголошення'}]({link}) {' '.join(tags)}".replace(",", " ")
            )
        NEW_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        NEW_PATH.write_text("", encoding="utf-8")

    log(f"Готово: {len(items)} у базі, нових {len(new_items)} (для сповіщення {len(notify)}), помилок {len(errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
