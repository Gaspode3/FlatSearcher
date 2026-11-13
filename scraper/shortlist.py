"""Шортлист для Claude: оцінює всі актуальні оголошення за критеріями сім'ї
(config.json → "advisor") і пише короткий docs/data/shortlist.md.

Цей файл читають чати проєкту «пошук квартири»:
https://gaspode3.github.io/FlatSearcher/data/shortlist.md
"""
import json, math, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
A = CFG.get("advisor", {})
SITE = CFG.get("site_url", "").rstrip("/")
EXCLUDED = set(CFG.get("excluded_sources", []))  # ключі й назви джерел збігаються (svitkvartyr)
KHRUSH = re.compile(r"хрущ", re.I)
PANEL = re.compile(r"панел", re.I)
CEIL = re.compile(r"стел\w*[^0-9]{0,25}([23][.,]\d{1,2})", re.I)  # «висота стелі 3,4 м» — пріоритет: високі стелі

NOT_LIVABLE = re.compile(r"під чистов|після будівельн|нульов|0-?й цикл|без ремонту|чорнов|стяжк", re.I)
COMMERCIAL = re.compile(r"комерційн|нежитлов|апартамент", re.I)
SHARED = re.compile(r"спільн\w* кухн|кухня[^.]{0,20}спільн|комунальн\w* квартир|підселенн", re.I)
GAS_IND = re.compile(r"індивідуальн\w* опаленн|газов\w* котел|двоконтурн|автономн\w* опаленн", re.I)
LIFT = re.compile(r"ліфт", re.I)
ASSIGN = re.compile(r"переуступ|переоступ|відступлен", re.I)
LIVABLE = re.compile(r"житлов\w* стан|можна (одразу )?заїжджати|заходь\w* і живи|з ремонтом|косметичн", re.I)


def txt(i):
    return " ".join(str(i.get(k) or "") for k in ("title", "description", "building", "condition"))


E = A.get("eoselia", {})
OLD_STOCK = re.compile(r"2011-2020|200\d|19\d\d|хрущ|чешк|австрійськ|польськ", re.I)


def money(x):
    return f"${x:,.0f}".replace(",", " ")


def months_to_repay(loan):
    """Скільки місяців платити позику під rate при внеску monthly_usd."""
    r, pay = E.get("rate", 0.07) / 12, E.get("monthly_usd", 1000)
    if loan <= 0: return 0
    if loan * r >= pay: return None
    return math.ceil(-math.log(1 - loan * r / pay) / math.log(1 + r))


def eoselia_plan(p):
    """Внесок, кредит і строк за єОселею для ціни p (у доларах)."""
    cash = E.get("cash_usd", 65000) - p * E.get("costs_pct", 0.025)
    down = max(min(cash, p), p * E.get("down_min_pct", 0.2))
    loan = max(p - down, 0)
    return down, loan, months_to_repay(loan)


def center(i, s, plus, minus, k=1.0):
    """Ближче до центру. center_km рахує scrape.py за точними координатами або за вулицею з оголошення."""
    km = i.get("center_km")
    if km is None:
        minus.append("відстань до центру невідома")
        return s
    near, far = A.get("center_near_km", 2.5), A.get("center_far_km", 4)
    tag = f"~{km:g} км до центру" + (" (за вулицею)" if i.get("geo_q") else "")
    if km <= near:
        s += 8 * k; plus.append(tag)
    elif km > far:
        s -= (km - far) * A.get("center_far_penalty_per_km", 5) * k; minus.append(tag)
    else:
        plus.append(tag)
    return s


def sale_score(i, track="cash"):
    """Повертає (бал, плюси, мінуси) або None, якщо не проходить жорсткі умови.
    track="cash" — вторинка за власні кошти; "eoselia" — новобудова під єОселю."""
    f = i.get("flags", {})
    r, a, p = i.get("rooms") or 0, i.get("area") or 0, i.get("price_usd") or 0
    t = txt(i)
    plus, minus, s = [], [], 50.0
    if track == "eoselia":
        budget, hard_max = E.get("price_target_usd", 95000), E.get("price_max_usd", 110000)
        if not p or p > hard_max or r < A.get("rooms_min", 2) or a < A.get("area_min", 55):
            return None
        m2, m2max = (p / a if a else 0), E.get("m2_max_usd", 1210)
        if m2 > m2max:
            s -= 20; minus.append(f"${m2:.0f}/м² — понад ліміт єОселі (~${m2max}/м²)")
        if a > E.get("area_max", 104):
            s -= 20; minus.append(f"{a:g} м² — понад норму площі єОселі")
        if OLD_STOCK.search(i.get("building") or "") or (i.get("year_built") and i["year_built"] < E.get("min_year", 2023)):
            s -= 25; minus.append("будинок, схоже, старший за 3 роки — під єОселю не піде")
        else:
            minus.append("перевірити рік введення (для єОселі ≤3 роки)")
        if f.get("under_construction"):
            s -= 15; minus.append("ще будується — одразу не заїхати")
        down, loan, m = eoselia_plan(p)
        if m is None:
            s -= 30; minus.append("кредит не вкладається в $1000/міс")
        else:
            s -= m / 6
            plus.append(f"внесок {money(down)}, кредит {money(loan)} → ~{m} міс по $1000")
    else:
        budget, hard_max = A.get("budget_usd", 65000), A.get("hard_max_usd", 72000)
        if not p or p > hard_max or r < A.get("rooms_min", 2) or a < A.get("area_min", 55):
            return None
        if f.get("under_construction"):
            return None
        if KHRUSH.search(i.get("building") or "") or KHRUSH.search(t):
            return None  # «що завгодно, тільки не хрущовки»
        if PANEL.search(i.get("building") or ""):
            s -= 10; minus.append("панельний будинок")
    if track == "eoselia":
        pass
    elif p <= budget:
        s += 10 + (budget - p) / 1000; plus.append(f"в бюджеті (${budget - p:,} запасу)".replace(",", " "))
    else:
        s -= (p - budget) / 500; minus.append(f"на ${p - budget:,} понад бюджет — торг".replace(",", " "))
    s += min((a - 55) * 0.8, 20)
    if a >= 65: plus.append(f"{a:g} м²")
    if r >= 3: s += 10; plus.append(f"{r} кімнати")
    if f.get("old_fund"): s += 12; plus.append("старий фонд (стелі, простір)")
    ceil = CEIL.search(t)
    if ceil:
        h = float(ceil.group(1).replace(",", "."))
        if h >= 3: s += 6; plus.append(f"стелі {h:g} м")
        elif h < 2.7: s -= 3; minus.append(f"стелі {h:g} м")
    if f.get("owner"): s += 6; plus.append("від власника")
    if re.search(r"ізольован|окрем\w* кімнат|не ?прохідн", t, re.I): s += 4; plus.append("ізольовані кімнати")
    if GAS_IND.search(t): s += 4; plus.append("індивідуальне/газове опалення")
    livable = not NOT_LIVABLE.search(t)
    if not livable:
        s -= A.get("not_livable_penalty", 15); minus.append("не житлова (під ремонт з нуля) — одразу не заїхати")
    elif LIVABLE.search(t):
        s += 4; plus.append("житловий стан")
    if f.get("apartments") or COMMERCIAL.search(t):
        s -= 25; minus.append("апартаменти/комерційне приміщення (тарифи, єОселя)")
    if SHARED.search(t):
        s -= 30; minus.append("спільна кухня / підселення")
    if ASSIGN.search(t):
        s -= 8; minus.append("переуступка — перевірити право власності")
    fl, fls = i.get("floor"), i.get("floors")
    if fl and fls:
        if fls <= 5 and fl >= 4 and not LIFT.search(t):
            s -= 3; minus.append(f"{fl}/{fls} без ліфта — важко з візочком")
        if fl == 1:
            minus.append("1 поверх (для нас ок, перевірити вологу)")
        if fl == fls and fls >= 5:
            s -= 2; minus.append("останній поверх")
    if track != "eoselia":
        s = center(i, s, plus, minus)
    fresh = i.get("fresh")
    if fresh is not None:
        s += (fresh - 60) / 5
    if i.get("suspicious"):
        s -= 8; minus.append("сумнівна свіжість: " + "; ".join(i.get("fresh_why") or [])[:120])
    if len(i.get("photos") or []) < 3:
        s -= 4; minus.append("мало або немає фото")
    return round(s, 1), plus, minus


def rent_score(i):
    f = i.get("flags", {})
    r, a, p = i.get("rooms") or 0, i.get("area") or 0, i.get("price_usd") or 0
    R = A.get("rent", {})
    budget = R.get("budget_usd", 650)
    if not p or p > R.get("hard_max_usd", 700) or r < R.get("rooms_min", 2):
        return None
    if f.get("apartments") and R.get("skip_apartments"):
        return None
    plus, minus, s = [], [], 50.0
    if p <= budget: s += 8 + (budget - p) / 25; plus.append(f"${p}/міс")
    else: s -= (p - budget) / 10; minus.append(f"${p - budget} понад бюджет")
    if a: s += min((a - 45) * 0.6, 18)
    if a >= 60: plus.append(f"{a:g} м²")
    if r >= 3: s += 8; plus.append(f"{r} кімнати")
    if f.get("owner") or f.get("no_commission"): s += 6; plus.append("без комісії/від власника")
    if f.get("power"): s += 4; plus.append("є живлення при відключеннях")
    if f.get("no_pets") and R.get("need_pets"): s -= 15; minus.append("без тварин")
    if re.search(r"без діт|не для сімей з діт", txt(i), re.I): s -= 30; minus.append("без дітей")
    s = center(i, s, plus, minus, k=0.5)
    fresh = i.get("fresh")
    if fresh is not None: s += (fresh - 60) / 5
    if i.get("suspicious"): minus.append("сумнівна свіжість")
    if len(i.get("photos") or []) < 3: s -= 4; minus.append("мало фото")
    return round(s, 1), plus, minus


def price_moves(i):
    h = [x.get("usd") for x in (i.get("price_history") or []) if x.get("usd")]
    if len(h) > 1 and abs(h[0] - h[-1]) / h[0] > 0.015:
        return f"; ціна: ${h[0]} → ${h[-1]}"
    return ""


def line(i, sc, mode):
    s, plus, minus = sc
    p = f"${i['price_usd']:,}".replace(",", " ") + ("/міс" if mode == "rent" else "")
    fl = f"{i.get('floor') or '?'}/{i.get('floors') or '?'}"
    where = ", ".join(x for x in (i.get("district"), i.get("address") if i.get("address") != "Львів" else i.get("street")) if x)[:70]
    link = f"{SITE}/#{'rent&' if mode == 'rent' else ''}id={i['id']}"
    desc = re.sub(r"\s+", " ", i.get("description") or "")[:350]
    out = [f"### {i['id']} · бал {s} · {p} · {i.get('rooms')}к · {i.get('area') or '?'} м² · пов. {fl}",
           f"- {i.get('title', '')[:100]} — {where or '—'}",
           f"- Будинок: {i.get('building') or '—'}; стан: {i.get('condition') or '—'}; продавець: {i.get('seller') or '—'}; джерело: {i['source']}"
           + (f" (також на {', '.join(x['source'] if isinstance(x, dict) else str(x) for x in i['also_on'])})" if i.get("also_on") else ""),
           f"- ➕ {'; '.join(plus) or '—'}",
           f"- ➖ {'; '.join(minus) or '—'}",
           f"- Свіжість {i.get('fresh')}/100; опубл. {i.get('published') or '?'}; вперше бачили {str(i.get('first_seen') or '')[:10]}"
           + price_moves(i),
           f"- Опис: {desc}",
           f"- Картка: {link} · Оригінал: {i['url']}", ""]
    return "\n".join(out)


def complex_rows(cxs):
    """Плани в зведених/частково зведених ЖК, що вкладаються в ліміти єОселі."""
    rows = []
    for cx in cxs:
        if re.search(r"апартамент", cx.get("name") or "", re.I):
            continue
        for pl in cx.get("plans") or []:
            p, a, r = pl.get("price_usd") or 0, pl.get("area") or 0, pl.get("rooms") or 0
            if not p or not a or r < A.get("rooms_min", 2) or a < A.get("area_min", 55):
                continue
            if p > E.get("price_max_usd", 110000) or p / a > E.get("m2_max_usd", 1210) * 1.1 or a > E.get("area_max", 104):
                continue
            down, loan, m = eoselia_plan(p)
            if m is None:
                continue
            ready = cx.get("status") in ("ready", "partly")
            rows.append((m + (0 if ready else 24), cx, pl, down, loan, m, ready))
    rows.sort(key=lambda x: x[0])
    seen, out = set(), []
    for row in rows:
        k = (row[1]["id"], row[2].get("rooms"))
        if k in seen: continue
        seen.add(k); out.append(row)
    return out


def complex_line(cx, pl, down, loan, m, ready):
    st = {"ready": "зданий", "partly": "частково зданий", "building": "будується"}.get(cx.get("status"), cx.get("status") or "?")
    inst = cx.get("installment") or {}
    return "\n".join([
        f"### {cx['name'].strip()} — {pl['rooms']}к {pl['area']:g} м² за {money(pl['price_usd'])} (${pl['price_usd']/pl['area']:.0f}/м²)",
        f"- Забудовник: {cx.get('developer') or '—'}; {cx.get('street') or ''}; статус: {st}; здача: {pl.get('release') or cx.get('release') or '—'}; поверх {pl.get('floors') or '?'}/{pl.get('building_floors') or cx.get('floors_max') or '?'}",
        f"- єОселя: внесок {money(down)}, кредит {money(loan)} → ~{m} міс по $1000"
        + (f"; розтермінування {inst.get('months') or '?'} міс, перший внесок {inst.get('first_pct')}%" if inst else "")
        + ("" if ready else "; ⚠ ще будується — жити одразу не вийде"),
        f"- {cx.get('url')} · картка: {SITE}/#id={cx['id']}", ""])


def load(path):
    p = ROOT / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}


def pick(items, fn, n):
    rows = []
    for i in items:
        if not i.get("active", True) or i.get("dup_of") or i.get("source") == "Вручну" or i.get("source") in EXCLUDED:
            continue
        sc = fn(i)
        if sc: rows.append((sc[0], i, sc))
    rows.sort(key=lambda x: -x[0])
    return rows[:n], len(rows)


def main():
    sale = load("docs/data/listings.json"); rent = load("docs/data/rent.json")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sec, sec_n = pick([i for i in sale["items"] if not i.get("flags", {}).get("new_build")], sale_score, A.get("top_sale", 15))
    nb, nb_n = pick([i for i in sale["items"] if i.get("flags", {}).get("new_build")],
                    lambda i: sale_score(i, "eoselia"), A.get("top_newbuild", 10))
    cx = complex_rows(sale.get("complexes") or [])
    rn, rn_n = pick(rent["items"], rent_score, A.get("top_rent", 12))
    md = [f"# Шортлист квартир (Львів) — {now}",
          "",
          "Автоматичний відбір для обговорення з Claude. Критерії — `criteria.md` у репозиторії, числа — config.json → advisor.",
          f"Дані: купівля оновлена {sale.get('updated')}, оренда {rent.get('updated')}. Курс ${sale.get('usd_rate')}.",
          f"Повні дані (усі поля, фото): {SITE}/data/listings.json і {SITE}/data/rent.json",
          "Бал — грубий фільтр, не вирок: читай плюси/мінуси й опис.", "",
          f"## Купівля — вторинка (топ {len(sec)} з {sec_n} відповідних)", ""]
    md += [line(i, sc, "sale") for _, i, sc in sec]
    md += [f"## Оренда (топ {len(rn)} з {rn_n})", ""]
    md += [line(i, sc, "rent") for _, i, sc in rn]
    md += [f"## Запасний варіант — новобудови під єОселю (топ {len(nb)} з {nb_n})", "",
           f"Розрахунок: власні ${E.get('cash_usd')} мінус ~{E.get('costs_pct', 0) * 100:g}% витрат на угоду, решта — кредит під {E.get('rate', 0) * 100:g}% з погашенням ${E.get('monthly_usd')}/міс.", ""]
    md += [line(i, sc, "sale") for _, i, sc in nb]
    md += [f"## Запасний варіант — плани від забудовників під єОселю (топ {min(len(cx), 12)} з {len(cx)} ЖК/типів)", ""]
    md += [complex_line(c, pl, d, l, m, rd) for _, c, pl, d, l, m, rd in cx[:12]]
    out = ROOT / "docs/data/shortlist.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"shortlist: вторинка {sec_n}, новобудови {nb_n}, оренда {rn_n} → {out}")


if __name__ == "__main__":
    sys.exit(main())
