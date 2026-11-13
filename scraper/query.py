"""API для чату з Claude: підбір квартир із зібраної бази з будь-якими фільтрами.

Приклади (з кореня репозиторію):
  python scraper/query.py search --max-price 65000 --min-area 60 --old-fund --max-km 3
  python scraper/query.py search --mode rent --max-price 650 --rooms 2 --new-days 2
  python scraper/query.py search --text "австрійськ|польськ" --sort m2 --limit 30
  python scraper/query.py show olx-939341144          # повна картка
  python scraper/query.py stats                       # що в базі, коли оновлено

База в репозиторії зашифрована: query.py сам розшифровує її паролем із FS_PASSWORD або файлу .fs_password.
--remote — завантажити зашифровану базу з сайту замість локальної.
Бал — та сама оцінка, що в shortlist.py (критерії сім'ї з config.json → advisor).
"""
import argparse, json, signal, math, re, sys, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortlist as S  # noqa: E402  (оцінка й константи; підтягує й приватний config)
import vault  # noqa: E402

ROOT, SITE, CFG = S.ROOT, S.SITE, S.CFG
FILES = {"sale": "docs/data/listings.json", "rent": "docs/data/rent.json"}
CENTER = next((c.get("center") for c in CFG.get("cities", []) if c.get("key") == "lviv"), [49.8419, 24.0315])
FLAG_ICONS = {"old_fund": "🏛", "owner": "👤", "needs_repair": "🔨", "fresh_renovation": "✨", "spacious": "📐",
              "no_commission": "🙅", "power": "🔋", "pets_ok": "🐾", "apartments": "⚠апарт", "new_build": "🏗",
              "under_construction": "🚧", "over_budget": "💸"}


def load(mode, remote=False):
    """База зашифрована (*.enc). Локально розшифровує сама (FS_PASSWORD або .fs_password); --remote — з сайту."""
    path = ROOT / FILES["sale" if mode == "newbuild" else mode]
    if remote:
        url = f"{SITE}/data/{path.name}.enc?ts={int(datetime.now().timestamp())}"
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(vault.decrypt_bytes(r.read().decode("utf-8"), vault.password()))
    enc = Path(str(path) + ".enc")
    if not path.exists() or (enc.exists() and enc.stat().st_mtime > path.stat().st_mtime):
        vault.decrypt_all(quiet=True)
    return json.loads(path.read_text(encoding="utf-8"))


def km(i):
    """Відстань до площі Ринок: center_km зі збирача або за координатами (тоді приблизно)."""
    if i.get("center_km") is not None:
        return i["center_km"], bool(i.get("geo_q"))
    if i.get("lat") and i.get("lon"):
        la1, lo1, la2, lo2 = map(math.radians, (CENTER[0], CENTER[1], i["lat"], i["lon"]))
        d = 2 * 6371 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
        return round(d, 1), bool(i.get("geo_approx"))
    return None, True


def score(i, mode):
    if mode == "rent":
        return S.rent_score(i)
    return S.sale_score(i, "eoselia" if mode == "newbuild" else "cash")


def ceiling(i):
    m = S.CEIL.search(S.txt(i))
    return float(m.group(1).replace(",", ".")) if m else None


def build_rows(data, mode, a):
    rx = re.compile(a.text, re.I) if a.text else None
    nrx = re.compile(a.exclude_text, re.I) if a.exclude_text else None
    since = (date.today() - timedelta(days=a.new_days)).isoformat() if a.new_days else None
    rows = []
    for i in data.get("items", []):
        f = i.get("flags", {})
        if not a.inactive and not i.get("active", True): continue
        if i.get("dup_of") or i.get("source") in S.EXCLUDED: continue
        if mode == "newbuild" and not f.get("new_build"): continue
        if mode == "sale" and f.get("new_build") and not a.with_newbuilds: continue
        p, ar, r = i.get("price_usd") or 0, i.get("area") or 0, i.get("rooms") or 0
        if a.max_price and p > a.max_price: continue
        if a.min_price and p < a.min_price: continue
        if a.rooms and r < a.rooms: continue
        if a.rooms_max and r > a.rooms_max: continue
        if a.min_area and ar < a.min_area: continue
        if a.max_area and ar > a.max_area: continue
        if a.max_m2 and (not ar or p / ar > a.max_m2): continue
        d, approx = km(i)
        if a.max_km is not None and (d is None or d > a.max_km): continue
        fl, fls = i.get("floor"), i.get("floors")
        if a.not_first and fl == 1: continue
        if a.not_last and fl and fls and fl == fls: continue
        if a.max_floor and fl and fl > a.max_floor: continue
        if a.district and not re.search(a.district, " ".join(str(i.get(k) or "") for k in ("district", "address", "street", "title")), re.I): continue
        t = S.txt(i)
        if rx and not rx.search(t): continue
        if nrx and nrx.search(t): continue
        if a.old_fund and not f.get("old_fund"): continue
        if a.owner and not (f.get("owner") or i.get("is_owner")): continue
        if a.no_commission and not (f.get("no_commission") or f.get("owner")): continue
        if a.pets and not f.get("pets_ok"): continue
        if a.no_panel and S.PANEL.search(i.get("building") or ""): continue
        if a.livable and S.NOT_LIVABLE.search(t): continue
        if a.gas and not S.GAS_IND.search(t): continue
        if a.min_ceiling:
            c = ceiling(i)
            if c is None or c < a.min_ceiling: continue
        if a.new_days and str(i.get("first_seen") or "")[:10] < since: continue
        if a.price_drop:
            h = [x.get("usd") for x in i.get("price_history") or [] if x.get("usd")]
            if not (len(h) > 1 and h[-1] < h[0]): continue
        sc = score(i, mode)
        if sc is None:
            if not a.all: continue  # жорсткі умови сім'ї (бюджет, площа, хрущовки…) — зніми через --all
            sc = (None, [], ["поза критеріями advisor"])
        rows.append({"i": i, "sc": sc, "km": d, "approx": approx})
    key = {
        "score": lambda x: -(x["sc"][0] if x["sc"][0] is not None else -999),
        "price": lambda x: x["i"].get("price_usd") or 0,
        "-price": lambda x: -(x["i"].get("price_usd") or 0),
        "area": lambda x: -(x["i"].get("area") or 0),
        "m2": lambda x: (x["i"].get("price_usd") or 0) / (x["i"].get("area") or 1e9) or 1e9,
        "km": lambda x: x["km"] if x["km"] is not None else 99,
        "new": lambda x: str(x["i"].get("first_seen") or ""),
    }[a.sort]
    rows.sort(key=key, reverse=(a.sort == "new"))
    return rows


def short(row, mode):
    i, (s, plus, minus) = row["i"], row["sc"]
    p, ar = i.get("price_usd") or 0, i.get("area") or 0
    price = f"${p:,}".replace(",", " ") + ("/міс" if mode == "rent" else f" (${p / ar:.0f}/м²)" if ar else "")
    d = f"{'~' if row['approx'] else ''}{row['km']:g} км" if row["km"] is not None else "? км"
    flags = "".join(v for k, v in FLAG_ICONS.items() if i.get("flags", {}).get(k))
    where = ", ".join(x for x in (i.get("district"), i.get("street") or (i.get("address") if i.get("address") != "Львів" else None)) if x)[:50]
    return (f"- **{i['id']}** · бал {s if s is not None else '—'} · {price} · {i.get('rooms')}к · {ar or '?'} м² · "
            f"пов. {i.get('floor') or '?'}/{i.get('floors') or '?'} · {d} · {where or '—'} {flags}\n"
            f"  {(i.get('title') or '')[:90]} | ➕ {'; '.join(plus[:4]) or '—'} | ➖ {'; '.join(minus[:4]) or '—'} "
            f"| з {str(i.get('first_seen') or '')[:10]}{S.price_moves(i)}{'' if i.get('active', True) else ' | ❌ неактивне'}")


def cmd_search(a):
    mode = a.mode
    data = load(mode, a.remote)
    rows = build_rows(data, mode, a)
    if a.format == "json":
        out = [{**{k: r["i"].get(k) for k in ("id", "price_usd", "rooms", "area", "floor", "floors", "district", "street", "title", "url", "first_seen", "source")},
                "score": r["sc"][0], "km": r["km"], "plus": r["sc"][1], "minus": r["sc"][2],
                "card": f"{SITE}/#{'rent&' if mode == 'rent' else ''}id={r['i']['id']}"} for r in rows[:a.limit]]
        print(json.dumps(out, ensure_ascii=False, indent=1)); return
    print(f"# {({'sale': 'Купівля', 'rent': 'Оренда', 'newbuild': 'Новобудови'})[mode]}: {len(rows)} знайдено, показано {min(len(rows), a.limit)} "
          f"(сортування: {a.sort}; база оновлена {data.get('updated')}, курс {data.get('usd_rate')})\n")
    for r in rows[:a.limit]:
        print(short(r, mode))
    if not rows:
        print("Нічого. Спробуй послабити фільтри або додати --all (без жорстких умов advisor).")


def find(id_):
    for mode in ("sale", "rent"):
        data = load(mode, ARGS.remote)
        for i in data.get("items", []):
            if i["id"] == id_ or i["id"].endswith("-" + id_) or id_ in (i.get("url") or ""):
                return i, mode
        for cx in data.get("complexes", []) or []:
            if cx["id"] == id_:
                return cx, "complex"
    return None, None


def cmd_show(a):
    for id_ in a.ids:
        i, mode = find(id_)
        if not i:
            print(f"## {id_}: не знайдено\n"); continue
        if mode == "complex":
            print(json.dumps(i, ensure_ascii=False, indent=1)[:6000]); continue
        sc = score(i, mode) or ["поза критеріями", [], []]
        d, approx = km(i)
        skip = {"photos", "description", "flags", "price_history", "fresh_why"}
        print(f"## {i['id']} — {i.get('title')}")
        print(f"Картка: {SITE}/#{'rent&' if mode == 'rent' else ''}id={i['id']}\nОригінал: {i.get('url')}")
        print(f"Бал: {sc[0]} | ➕ {'; '.join(sc[1]) or '—'} | ➖ {'; '.join(sc[2]) or '—'}")
        print(f"До центру: {d if d is not None else '?'} км{' (приблизно)' if approx else ''}; стелі з опису: {ceiling(i) or '—'}")
        for k, v in i.items():
            if k not in skip and v not in (None, "", [], {}):
                print(f"- {k}: {v}")
        print(f"- мітки: {', '.join(k for k, v in (i.get('flags') or {}).items() if v) or '—'}")
        print(f"- історія ціни: {', '.join(str(x.get('date')) + ' $' + str(x.get('usd')) for x in i.get('price_history') or [])}")
        if i.get("fresh_why"): print(f"- свіжість: {'; '.join(i['fresh_why'])}")
        ph = i.get("photos") or []
        print(f"- фото ({len(ph)}): " + " ".join(ph[:a.photos]))
        print(f"\nОпис:\n{i.get('description') or '—'}\n")


def cmd_stats(a):
    for mode in ("sale", "rent"):
        d = load(mode, a.remote)
        it = [i for i in d.get("items", []) if i.get("active", True) and not i.get("dup_of")]
        today = date.today().isoformat()
        new = sum(1 for i in it if str(i.get("first_seen"))[:10] >= (date.today() - timedelta(days=1)).isoformat())
        ok = sum(1 for i in it if (score(i, mode) if not (mode == "sale" and i.get("flags", {}).get("new_build")) else None))
        src = {}
        for i in it: src[i["source"]] = src.get(i["source"], 0) + 1
        print(f"{mode}: оновлено {d.get('updated')} | активних {len(it)} (з них за критеріями сім'ї {ok}, нових за 2 дні {new}) | "
              f"джерела {src} | помилки збору: {d.get('errors') or 'немає'} | сьогодні {today}")


def cmd_link(a):
    """Одне посилання на добірку: SITE/#list=id1,id2&t=Назва (оренда — #rent&list=)."""
    from urllib.parse import quote
    ids = [x.strip() for raw in a.ids for x in raw.split(",") if x.strip()]
    url = f"{SITE}/#{'rent&' if a.mode == 'rent' else ''}list=" + ",".join(quote(x, safe="") for x in ids)
    if a.title:
        url += "&t=" + quote(a.title, safe="")
    print(url)


def main():
    global ARGS
    if hasattr(signal, "SIGPIPE"): signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # спокійно з | head
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--remote", action="store_true", help="брати дані з сайту, а не з локальних файлів")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("search", help="пошук із фільтрами")
    s.add_argument("--mode", choices=["sale", "rent", "newbuild"], default="sale")
    s.add_argument("--max-price", type=int); s.add_argument("--min-price", type=int)
    s.add_argument("--rooms", type=int, help="мінімум кімнат"); s.add_argument("--rooms-max", type=int)
    s.add_argument("--min-area", type=float); s.add_argument("--max-area", type=float)
    s.add_argument("--max-m2", type=float, help="макс. $ за м²")
    s.add_argument("--max-km", type=float, help="макс. км до пл. Ринок")
    s.add_argument("--district", help="регулярний вираз по району/вулиці/заголовку")
    s.add_argument("--text", help="регулярний вираз по заголовку+опису (напр. 'австрійськ|польськ')")
    s.add_argument("--exclude-text", help="відкинути, якщо є в тексті")
    s.add_argument("--old-fund", action="store_true"); s.add_argument("--owner", action="store_true")
    s.add_argument("--no-commission", action="store_true"); s.add_argument("--pets", action="store_true")
    s.add_argument("--no-panel", action="store_true"); s.add_argument("--livable", action="store_true", help="без «0-й цикл/після будівельників»")
    s.add_argument("--gas", action="store_true", help="згадка індивідуального/газового опалення")
    s.add_argument("--min-ceiling", type=float, help="стелі з опису, м")
    s.add_argument("--not-first", action="store_true"); s.add_argument("--not-last", action="store_true")
    s.add_argument("--max-floor", type=int)
    s.add_argument("--new-days", type=int, help="з'явились за останні N днів")
    s.add_argument("--price-drop", action="store_true", help="ціна знижувалась")
    s.add_argument("--with-newbuilds", action="store_true", help="у купівлі показувати й новобудови")
    s.add_argument("--inactive", action="store_true", help="включно зі знятими")
    s.add_argument("--all", action="store_true", help="не відкидати за жорсткими умовами advisor")
    s.add_argument("--sort", choices=["score", "price", "-price", "area", "m2", "km", "new"], default="score")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--format", choices=["md", "json"], default="md")
    sh = sp.add_parser("show", help="повна картка за id (можна кілька)")
    sh.add_argument("ids", nargs="+"); sh.add_argument("--photos", type=int, default=6)
    sp.add_parser("stats", help="стан бази")
    lk = sp.add_parser("link", help="одне посилання на добірку квартир")
    lk.add_argument("ids", nargs="+"); lk.add_argument("--title", "-t", default="")
    lk.add_argument("--mode", choices=["sale", "rent"], default="sale")
    ARGS = p.parse_args()
    {"search": cmd_search, "show": cmd_show, "stats": cmd_stats, "link": cmd_link}[ARGS.cmd](ARGS)


if __name__ == "__main__":
    main()
