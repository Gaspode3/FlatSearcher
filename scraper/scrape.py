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


def get(url: str, *, params: dict | None = None, as_json: bool = False, tries: int = 3, headers: dict | None = None):
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
PHONE_RE = re.compile(r"(?<!\d)(?:\+?3\s?8\s?)?\(?0\s?\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")


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
    oc = olx_resolve_city(city)
    if not oc:
        return scrape_olx_mobile(cfg, rate, city)
    items = []
    per = 50
    for page in range(cfg["olx"].get("pages", 10)):
        params = {"offset": page * per, "limit": per, "category_id": 1758, "city_id": oc["city_id"],
                  "sort_by": "created_at:desc"}
        if oc.get("region_id"):
            params["region_id"] = oc["region_id"]
        data = get("https://www.olx.ua/api/v1/offers/", params=params, as_json=True)
        if data is None and page == 0:
            log("  OLX API відмовив — беру мобільну версію сайту")
            return scrape_olx_mobile(cfg, rate, city)
        ads = (data or {}).get("data") or []
        got = [olx_item(ad, rate, city) for ad in ads]
        items.extend(got)
        log(f"  OLX {city['name']} стор. {page + 1}: {len(got)}, підходять {sum(passes(x, cfg) for x in got)}")
        if len(ads) < per:
            break
        time.sleep(1.5)
    return items


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
                )
                found += 1
            log(f"  DOM.RIA {base} стор. {page}: {found}")
            if not found:
                break
            time.sleep(1.5)
    out = list(items.values())
    enrich(out, cfg, "DOM.RIA", ria_detail, cfg["domria"].get("enrich_per_run", 50))
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
                )
                found += 1
            log(f"  rieltor {city['name']} {rooms}к стор. {page}: {found}")
            if not found or len(cards) < 10:
                break
            time.sleep(1.5)
    out = list(items.values())
    enrich(out, cfg, "rieltor.ua", rieltor_detail, cfg["rieltor"].get("enrich_per_run", 40))
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
                )
                found += 1
            log(f"  svitkvartyr {base} стор. {page}: {found}")
            if not found:
                break
            time.sleep(1.5)
    return list(items.values())


# ================================================================ спільне: сторінка оголошення, геокодування

def enrich(items: list[dict], cfg: dict, name: str, fn: Callable[[dict, str], None], limit: int) -> None:
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
        time.sleep(1)
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
    args = ap.parse_args()
    DEBUG = args.debug
    DATA_PATH = ROOT / args.out
    NEW_PATH = ROOT / args.new
    CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    existing = load_existing()
    KNOWN = {it["id"]: it for it in existing.get("items", [])}
    rate = usd_rate()
    log(f"Курс USD: {rate:.2f}")

    scraped: list[dict] = []
    errors: list[str] = []
    ok_sources: set[tuple[str, str]] = set()
    wanted = [w.strip() for w in (args.sources or ",".join(CFG["default_sources"])).split(",") if w.strip()]
    cities = [c for c in CFG["cities"] if c.get("enabled")]
    for city in cities:
        for key in wanted:
            if key not in SOURCES:
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

    try:
        geocode(scraped, CFG)
    except Exception as e:  # noqa: BLE001
        log("  ! геокодування:", e)

    by_id = {it["id"]: it for it in existing.get("items", [])}
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
    items.sort(key=lambda x: (-x.get("score", 0), x.get("price_usd") or 0))

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "usd_rate": round(rate, 2),
        "criteria": {k: CFG[k] for k in ("rooms_min", "rooms_max", "budget_usd", "max_price_usd", "default_rooms")},
        "cities": [{"key": c["key"], "name": c["name"]} for c in cities],
        "errors": errors,
        "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    NEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    notify = [it for it in new_items if (it.get("rooms") or 0) >= CFG["notify_rooms_min"] and not it["flags"]["over_budget"]]
    if notify:
        notify.sort(key=lambda x: -x["score"])
        site = CFG.get("site_url", "").rstrip("/")
        lines = [f"Нових варіантів у бюджеті ({CFG['notify_rooms_min']}+ кімнати): **{len(notify)}**\n"]
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
