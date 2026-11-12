#!/usr/bin/env python3
"""Збирач оголошень про продаж квартир у Львові (OLX + DOM.RIA).

Запуск:  python scraper/scrape.py            # звичайний запуск
         python scraper/scrape.py --debug    # зберігає сирі сторінки в debug/ для налагодження

Результат: docs/data/listings.json (дані для сайту) і new_listings.md (нові варіанти
для сповіщення; файл порожній, якщо нічого нового).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "docs" / "data" / "listings.json"
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


def log(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def get(url: str, *, params: dict | None = None, as_json: bool = False, tries: int = 3):
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
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


# ---------------------------------------------------------------- helpers

def num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(" ", " ").replace(" ", "").replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def usd_rate() -> float:
    """Курс НБУ гривні до долара; запасне значення — 46."""
    data = get(
        "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange",
        params={"valcode": "USD", "json": ""},
        as_json=True,
    )
    try:
        return float(data[0]["rate"])
    except Exception:  # noqa: BLE001
        log("  ! не вдалося отримати курс НБУ, беру 46")
        return 46.0


OLD_FUND_RE = re.compile(
    r"австр|польськ|польська будова|довоєн|старий фонд|стар(ий|а|ому) цегл|"
    r"кам['’]?яниц|сталінк|висок\w* стел|стел\w*\s*(3[.,]\d|3\.?\d?\s*м|[34][.,]\d)|"
    r"\b(18[5-9]\d|19[0-3]\d)\s*(р|рік|року)",
    re.I,
)
REPAIR_RE = re.compile(
    r"під ремонт|без ремонту|потребує ремонту|старий ремонт|житловий стан|"
    r"косметичн\w* ремонт|радянськ\w* ремонт|під чистов|після будівельн|під оздоблення|"
    r"ремонт на свій смак",
    re.I,
)
FRESH_RE = re.compile(r"євроремонт|дизайнерськ\w* ремонт|авторськ\w* (ремонт|проєкт|проект)|новий ремонт|свіжий ремонт", re.I)
OWNER_RE = re.compile(r"від власник|без посередник|без комісі|без ріелтор|без рієлтор|без ан\b|власник", re.I)
AGENCY_RE = re.compile(r"агенц|ріелтор\s+\w+|рієлтор\s+\w+|комісія\s+\d|ан\s+[«\"]", re.I)
APART_RE = re.compile(r"апартамент", re.I)
UNDER_CONSTR_RE = re.compile(r"переуступк|будується|здача (в|у)?\s*(експлуатац|\d)|0 цикл", re.I)


PHONE_RE = re.compile(r"(?<!\d)(?:\+?3\s?8\s?)?\(?0\s?\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")


def phones_from_text(text: str) -> list[str]:
    """Телефони, які продавець написав прямо в описі."""
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
        "old_fund": bool(OLD_FUND_RE.search(text)) or (year is not None and year < 1945),
        "needs_repair": bool(REPAIR_RE.search(text)),
        "fresh_renovation": bool(FRESH_RE.search(text)),
        "owner": item.get("is_owner") if item.get("is_owner") is not None else bool(OWNER_RE.search(text)),
        "apartments": bool(APART_RE.search(text)),
        "under_construction": bool(UNDER_CONSTR_RE.search(text)),
        "spacious": (item.get("area") or 0) >= cfg["spacious_area_m2"],
        "over_budget": (item.get("price_usd") or 0) > cfg["budget_usd"],
    }
    if flags["owner"] and AGENCY_RE.search(text) and item.get("is_owner") is None:
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


# ---------------------------------------------------------------- OLX

def _find_ads(obj: Any) -> list[dict]:
    """Рекурсивно шукає список оголошень у JSON-стані сторінки OLX."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and all("url" in x and "title" in x for x in obj[:3]):
            return obj
        for x in obj:
            found = _find_ads(x)
            if found:
                return found
    elif isinstance(obj, dict):
        for key in ("ads", "data"):
            if key in obj:
                found = _find_ads(obj[key])
                if found:
                    return found
        for v in obj.values():
            found = _find_ads(v)
            if found:
                return found
    return []


def _olx_param(params: list[dict], *patterns: str) -> Any:
    for p in params or []:
        hay = f"{p.get('key', '')} {p.get('name', '')}".lower()
        if any(re.search(pt, hay) for pt in patterns):
            nv = p.get("normalizedValue")
            v = p.get("value")
            if isinstance(v, dict):
                v = v.get("label") or v.get("key") or v.get("value")
            return nv if nv not in (None, "") else v
    return None


def _olx_photo(p: Any) -> str | None:
    link = p.get("link") if isinstance(p, dict) else p
    if not link:
        return None
    return link.replace("{width}", "800").replace("{height}", "600")


def parse_olx_state(html: str, rate: float) -> list[dict]:
    m = re.search(r"window\.__PRERENDERED_STATE__\s*=\s*(\".*?\");\s*\n", html, re.S)
    if not m:
        m = re.search(r"window\.__PRERENDERED_STATE__\s*=\s*(\".*?\");", html, re.S)
    if not m:
        return []
    try:
        state = json.loads(json.loads(m.group(1)))
    except Exception as e:  # noqa: BLE001
        log("  ! OLX: не вдалося розібрати стан сторінки:", e)
        return []
    out = []
    for ad in _find_ads(state):
        params = ad.get("params") or []
        price = ad.get("price") or {}
        reg = price.get("regularPrice") or {}
        value, cur = num(reg.get("value")), (reg.get("currencyCode") or "UAH").upper()
        price_usd = value if cur == "USD" else (value / rate if value and cur == "UAH" else None)
        if value and cur == "EUR":
            price_usd = value * 1.08
        loc = ad.get("location") or {}
        desc = re.sub(r"<[^>]+>", " ", ad.get("description") or "")
        is_business = ad.get("isBusiness")
        if is_business is None:
            pb = _olx_param(params, r"private_business")
            is_business = None if pb is None else str(pb).lower() == "business"
        out.append({
            "id": f"olx-{ad.get('id')}",
            "source": "OLX",
            "url": ad.get("url"),
            "title": ad.get("title"),
            "price_usd": round(price_usd) if price_usd else None,
            "price_orig": f"{value:,.0f} {cur}".replace(",", " ") if value else None,
            "rooms": int(num(_olx_param(params, r"number_of_rooms", r"кімнат")) or 0) or None,
            "area": num(_olx_param(params, r"total_area", r"загальна площа")),
            "floor": int(num(_olx_param(params, r"^floor\b", r"^floor ", r"\bповерх\b(?!ов)")) or 0) or None,
            "floors": int(num(_olx_param(params, r"total_floors", r"floor_count", r"поверховість")) or 0) or None,
            "building": " ".join(str(x) for x in [
                _olx_param(params, r"property_type", r"тип будинку", r"тип стін", r"wall"),
                _olx_param(params, r"year", r"рік"),
            ] if x),
            "condition": str(_olx_param(params, r"repair", r"ремонт", r"стан") or ""),
            "year_built": int(num(_olx_param(params, r"year", r"рік побудов")) or 0) or None,
            "district": loc.get("districtName") or (loc["district"].get("name") if isinstance(loc.get("district"), dict) else None),
            "address": loc.get("cityName") or "Львів",
            "photos": [u for u in (_olx_photo(p) for p in (ad.get("photos") or [])[:15]) if u],
            "seller": (ad.get("user") or {}).get("name") or (ad.get("contact") or {}).get("name"),
            "seller_since": ((ad.get("user") or {}).get("created") or "")[:4] or None,
            "description": desc.strip()[:1200],
            "published": (ad.get("createdTime") or ad.get("lastRefreshTime") or "")[:10] or None,
            "is_owner": (not is_business) if is_business is not None else None,
        })
    return out


def parse_olx_cards(html: str, rate: float) -> list[dict]:
    """Запасний розбір карток, якщо стан сторінки змінився."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select('[data-cy="l-card"]'):
        a = card.find("a", href=True)
        if not a:
            continue
        url = a["href"]
        if url.startswith("/"):
            url = "https://www.olx.ua" + url
        title_el = card.find(["h4", "h6"])
        price_el = card.select_one('[data-testid="ad-price"]')
        img = card.find("img")
        text = card.get_text(" ", strip=True)
        price_text = price_el.get_text(" ", strip=True) if price_el else ""
        value = num(price_text)
        usd = value if "$" in price_text else (value / rate if value else None)
        area = num((re.search(r"(\d+[.,]?\d*)\s*м²", text) or [None, None])[1])
        out.append({
            "id": "olx-" + hashlib.md5(url.split("#")[0].encode()).hexdigest()[:12],
            "source": "OLX",
            "url": url.split("#")[0],
            "title": title_el.get_text(" ", strip=True) if title_el else text[:80],
            "price_usd": round(usd) if usd else None,
            "price_orig": price_text or None,
            "rooms": None,
            "area": area,
            "photos": [img.get("src")] if img and img.get("src") and img["src"].startswith("http") else [],
            "description": "",
            "district": (re.search(r"Львів,\s*([^-–]+)", text) or [None, None])[1],
            "address": "Львів",
        })
    return out


def scrape_olx(cfg: dict, rate: float) -> list[dict]:
    items: list[dict] = []
    for base in cfg["olx"]["search_urls"]:
        for page in range(1, cfg["olx"]["pages"] + 1):
            url = base + (f"&page={page}" if page > 1 else "")
            html = get(url)
            if not html:
                break
            ads = parse_olx_state(html, rate) or parse_olx_cards(html, rate)
            log(f"  OLX стор. {page}: {len(ads)}")
            if not ads:
                break
            items.extend(ads)
            time.sleep(2)
    # повна сторінка оголошення: опис і фото для тих, кому їх бракує
    for it in items:
        if passes(it, CFG) and it.get("url") and (
            not it.get("description") or not it.get("rooms") or len(it.get("photos") or []) < 3 or not it.get("seller")
        ):
            html = get(it["url"])
            if html:
                full = parse_olx_state(html, rate)
                match = next((x for x in full if x.get("url") and it["url"].startswith(x["url"].split("?")[0])), None)
                if match:
                    for k, v in match.items():
                        if v and (not it.get(k) or (k in ("photos", "description") and len(v) > len(it[k]))):
                            it[k] = v
                else:
                    soup = BeautifulSoup(html, "html.parser")
                    d = soup.select_one('[data-cy="ad_description"]')
                    if d:
                        it["description"] = d.get_text(" ", strip=True)[:1200]
            time.sleep(1.5)
    return items


# ---------------------------------------------------------------- DOM.RIA

def _ria_photo(path: str | None) -> str | None:
    if not path:
        return None
    path = re.sub(r"\.(jpg|jpeg|png|webp)$", "", path)
    return f"https://cdn.riastatic.com/photos/{path}xl.jpg"


def _ria_item_from_info(info: dict, rate: float) -> dict:
    price_usd = None
    arr = info.get("priceArr") or {}
    if arr.get("1"):
        price_usd = num(arr["1"])
    elif info.get("price") and info.get("currency_type", "").lower() in ("$", "usd"):
        price_usd = num(info["price"])
    elif info.get("price") and info.get("currency_type", "").lower() in ("грн", "uah"):
        price_usd = num(info["price"]) / rate
    photos_obj = info.get("photos") or {}
    photos = []
    if isinstance(photos_obj, dict):
        for p in list(photos_obj.values())[:15]:
            photos.append(_ria_photo(p.get("file") if isinstance(p, dict) else None))
    if not photos and info.get("main_photo"):
        photos.append(_ria_photo(info["main_photo"]))
    url = info.get("beautiful_url") or ""
    if url and not url.startswith("http"):
        url = "https://dom.ria.com/uk/" + url.lstrip("/")
    desc = info.get("description_uk") or info.get("description") or ""
    agency = info.get("agency") or info.get("agency_id")
    return {
        "id": f"ria-{info.get('realty_id') or info.get('id')}",
        "source": "DOM.RIA",
        "url": url,
        "title": " ".join(str(x) for x in [info.get("street_name_uk") or info.get("street_name"), info.get("building_number_str")] if x) or "Квартира",
        "price_usd": round(price_usd) if price_usd else None,
        "price_orig": None,
        "rooms": int(info["rooms_count"]) if info.get("rooms_count") else None,
        "area": num(info.get("total_square_meters")),
        "floor": info.get("floor"),
        "floors": info.get("floors_count"),
        "building": " ".join(str(x) for x in [info.get("wall_type_uk") or info.get("wall_type"), info.get("year_of_construction")] if x),
        "year_built": int(num(info.get("year_of_construction")) or 0) or None,
        "condition": "",
        "district": info.get("district_name_uk") or info.get("district_name"),
        "address": "Львів, " + " ".join(str(x) for x in [info.get("street_name_uk") or info.get("street_name"), info.get("building_number_str")] if x),
        "photos": [p for p in photos if p],
        "description": re.sub(r"\s+", " ", desc).strip()[:1200],
        "published": (info.get("publishing_date") or "")[:10] or None,
        "is_owner": False if agency else None,
        "seller": (info.get("user") or {}).get("name") or info.get("user_name") or info.get("agency_name"),
        "phones": _ria_phones(info),
    }


def _ria_phones(info: dict) -> list[str]:
    out = []
    for src in (info.get("phones"), (info.get("user") or {}).get("phones")):
        for p in src or []:
            if isinstance(p, dict):
                p = p.get("phone_formatted") or p.get("phone_num") or p.get("phone")
            if p:
                out.append(str(p))
    return list(dict.fromkeys(out))


def scrape_domria_api(cfg: dict, rate: float, key: str) -> list[dict]:
    """Ощадливо щодо ліміту API (1000 запитів/міс.): 1 пошуковий запит на день
    і запит деталей лише для оголошень, яких ще немає в базі."""
    rc = cfg["domria"]
    ids: list[int] = []
    used = 0
    for page in range(rc.get("api_pages", 1)):
        params = {
            "api_key": key, "category": 1, "realty_type": 2, "operation_type": 1,
            "state_id": rc["state_id"], "city_id": rc["city_id"], "page": page,
            "characteristic[209][from]": cfg["rooms_min"], "characteristic[209][to]": cfg["rooms_max"],
            "sort": "created_at",
        }
        data = get("https://developers.ria.com/dom/search", params=params, as_json=True, tries=1)
        used += 1
        if not data or not data.get("items"):
            break
        ids.extend(data["items"])
        log(f"  DOM.RIA API стор. {page}: {len(data['items'])}")
    items = []
    limit = rc.get("api_info_per_run", 25)
    skipped = 0
    for rid in dict.fromkeys(ids):
        known = KNOWN.get(f"ria-{rid}")
        if known:
            items.append(dict(known))
            continue
        if used >= limit:
            skipped += 1
            continue
        info = get(f"https://developers.ria.com/dom/info/{rid}", params={"api_key": key, "lang_id": 4}, as_json=True, tries=1)
        used += 1
        if info:
            items.append(_ria_item_from_info(info, rate))
        time.sleep(0.4)
    log(f"  DOM.RIA API: використано запитів {used}" + (f", відкладено {skipped} нових до наступного запуску" if skipped else ""))
    return items


RIA_LINK_RE = re.compile(r"/uk/realty-prodaja-kvartira-[\w-]+-(\d+)\.html")


def scrape_domria_html(cfg: dict, rate: float) -> list[dict]:
    items: dict[str, dict] = {}
    for base in cfg["domria"]["html_urls"]:
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
                # картка — найближчий предок, де є ціна
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
                imgs = [i.get("src") for i in card.find_all("img") if (i.get("src") or "").startswith("http")]
                items[rid] = {
                    "id": rid,
                    "source": "DOM.RIA",
                    "url": "https://dom.ria.com" + m.group(0),
                    "title": a.get("title") or a.get_text(" ", strip=True)[:100],
                    "price_usd": round(price) if price else None,
                    "rooms": int(rooms) if rooms else None,
                    "area": area,
                    "floor": int(fl.group(1)) if fl else None,
                    "floors": int(fl.group(2)) if fl else None,
                    "photos": imgs[:6],
                    "description": text[:1200],
                    "district": None,
                    "address": a.get("title") or "",
                    "is_owner": True if "bez-rieltora" in base else None,
                }
                found += 1
            log(f"  DOM.RIA HTML {base} стор. {page}: {found}")
            if not found:
                break
            time.sleep(2)
    return list(items.values())


def scrape_domria(cfg: dict, rate: float) -> list[dict]:
    key = os.environ.get("DOMRIA_API_KEY", "").strip()
    if key:
        try:
            items = scrape_domria_api(cfg, rate, key)
            if items:
                return items
            log("  ! DOM.RIA API нічого не повернуло, пробую HTML")
        except Exception as e:  # noqa: BLE001
            log("  ! DOM.RIA API помилка:", e)
    return scrape_domria_html(cfg, rate)


# ---------------------------------------------------------------- merge & save

def load_existing() -> dict:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return {"items": []}


def main() -> int:
    global DEBUG, CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    DEBUG = args.debug
    CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    global KNOWN
    KNOWN = {it["id"]: it for it in load_existing().get("items", [])}
    rate = usd_rate()
    log(f"Курс USD: {rate:.2f}")

    scraped: list[dict] = []
    errors: list[str] = []
    for name, fn, enabled in (
        ("OLX", scrape_olx, CFG["olx"]["enabled"]),
        ("DOM.RIA", scrape_domria, CFG["domria"]["enabled"]),
    ):
        if not enabled:
            continue
        log(f"→ {name}")
        try:
            got = fn(CFG, rate)
            log(f"  всього з {name}: {len(got)}")
            scraped.extend(got)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            log(f"  ! {name} впав: {e}")

    existing = load_existing()
    by_id = {it["id"]: it for it in existing.get("items", [])}
    new_items = []
    for it in scraped:
        if not it.get("id") or not it.get("url") or not passes(it, CFG):
            continue
        it["flags"] = make_flags(it, CFG)
        it["score"] = score(it)
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
        else:
            it["first_seen"] = TODAY
            it["price_history"] = [{"date": TODAY, "usd": it["price_usd"]}]
            new_items.append(it)
        it["last_seen"] = TODAY
        it["active"] = True
        by_id[it["id"]] = it

    cutoff = (dt.date.today() - dt.timedelta(days=CFG["keep_inactive_days"])).isoformat()
    # API DOM.RIA щодня бачить лише першу сторінку свіжих оголошень, тож старіші
    # оголошення звідти вважаємо активними ще 30 днів після останньої появи.
    ria_cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    for it in by_id.values():
        if it.get("source") == "Вручну" or it.get("last_seen", TODAY) >= TODAY:
            continue
        if it["id"].startswith("ria-") and os.environ.get("DOMRIA_API_KEY") and it.get("last_seen", TODAY) >= ria_cutoff:
            continue
        it["active"] = False
    items = [it for it in by_id.values() if it.get("active") or it.get("last_seen", TODAY) >= cutoff or it.get("source") == "Вручну"]
    items.sort(key=lambda x: (-x.get("score", 0), x.get("price_usd") or 0))

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "usd_rate": round(rate, 2),
        "criteria": {k: CFG[k] for k in ("rooms_min", "rooms_max", "budget_usd", "max_price_usd")},
        "errors": errors,
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if new_items:
        new_items.sort(key=lambda x: -x["score"])
        lines = [f"Знайдено нових варіантів: **{len(new_items)}**\n"]
        for it in new_items[:30]:
            tags = [t for t, on in (("🏛 старий фонд", it["flags"]["old_fund"]), ("🔨 під ремонт", it["flags"]["needs_repair"]),
                                    ("👤 власник", it["flags"]["owner"]), ("💸 вище бюджету", it["flags"]["over_budget"])) if on]
            lines.append(
                f"- **${it['price_usd']:,}** · {it.get('rooms') or '?'} кімн. · {it.get('area') or '?'} м² · "
                f"{it.get('district') or ''} — [{it.get('title') or 'оголошення'}]({it['url']}) {' '.join(tags)}".replace(",", " ")
            )
        NEW_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        NEW_PATH.write_text("", encoding="utf-8")

    log(f"Готово: {len(items)} у базі, нових {len(new_items)}, помилок {len(errors)}")
    return 0


CFG: dict = {}
KNOWN: dict = {}

if __name__ == "__main__":
    sys.exit(main())
