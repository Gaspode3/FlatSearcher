"""Шортлист для Claude: оцінює всі актуальні оголошення за критеріями сім'ї
(config.json → "advisor") і пише короткий docs/data/shortlist.md.

Цей файл читають чати проєкту «пошук квартири»:
https://gaspode3.github.io/FlatSearcher/data/shortlist.md
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
A = CFG.get("advisor", {})
SITE = CFG.get("site_url", "").rstrip("/")

NOT_LIVABLE = re.compile(r"під чистов|після будівельн|нульов|0-?й цикл|без ремонту|чорнов|стяжк", re.I)
COMMERCIAL = re.compile(r"комерційн|нежитлов|апартамент", re.I)
SHARED = re.compile(r"спільн\w* кухн|кухня[^.]{0,20}спільн|комунальн\w* квартир|підселенн", re.I)
GAS_IND = re.compile(r"індивідуальн\w* опаленн|газов\w* котел|двоконтурн|автономн\w* опаленн", re.I)
LIFT = re.compile(r"ліфт", re.I)
ASSIGN = re.compile(r"переуступ|переоступ|відступлен", re.I)
LIVABLE = re.compile(r"житлов\w* стан|можна (одразу )?заїжджати|заходь\w* і живи|з ремонтом|косметичн", re.I)


def txt(i):
    return " ".join(str(i.get(k) or "") for k in ("title", "description", "building", "condition"))


def sale_score(i):
    """Повертає (бал, плюси, мінуси) або None, якщо не проходить жорсткі умови."""
    f = i.get("flags", {})
    r, a, p = i.get("rooms") or 0, i.get("area") or 0, i.get("price_usd") or 0
    budget, hard_max = A.get("budget_usd", 65000), A.get("hard_max_usd", 72000)
    if not p or p > hard_max or r < A.get("rooms_min", 2) or a < A.get("area_min", 55):
        return None
    if f.get("under_construction"):
        return None
    t = txt(i)
    plus, minus, s = [], [], 50.0
    if p <= budget:
        s += 10 + (budget - p) / 1000; plus.append(f"в бюджеті (${budget - p:,} запасу)".replace(",", " "))
    else:
        s -= (p - budget) / 500; minus.append(f"на ${p - budget:,} понад бюджет — торг".replace(",", " "))
    s += min((a - 55) * 0.8, 20)
    if a >= 65: plus.append(f"{a:g} м²")
    if r >= 3: s += 10; plus.append(f"{r} кімнати")
    if f.get("old_fund"): s += 8; plus.append("старий фонд (стелі, простір)")
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
            s -= 6; minus.append(f"{fl}/{fls} без ліфта — важко з візочком")
        if fl == 1:
            s -= 3; minus.append("1 поверх")
        if fl == fls and fls >= 5:
            s -= 2; minus.append("останній поверх")
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
    where = ", ".join(x for x in (i.get("district"), i.get("address")) if x and x != "Львів")[:70]
    link = f"{SITE}/#{'rent&' if mode == 'rent' else ''}id={i['id']}"
    desc = re.sub(r"\s+", " ", i.get("description") or "")[:350]
    out = [f"### {i['id']} · бал {s} · {p} · {i.get('rooms')}к · {i.get('area') or '?'} м² · пов. {fl}",
           f"- {i.get('title', '')[:100]} — {where or '—'}",
           f"- Будинок: {i.get('building') or '—'}; стан: {i.get('condition') or '—'}; продавець: {i.get('seller') or '—'}; джерело: {i['source']}"
           + (f" (також на {', '.join(i['also_on'])})" if i.get("also_on") else ""),
           f"- ➕ {'; '.join(plus) or '—'}",
           f"- ➖ {'; '.join(minus) or '—'}",
           f"- Свіжість {i.get('fresh')}/100; опубл. {i.get('published') or '?'}; вперше бачили {str(i.get('first_seen') or '')[:10]}"
           + price_moves(i),
           f"- Опис: {desc}",
           f"- Картка: {link} · Оригінал: {i['url']}", ""]
    return "\n".join(out)


def load(path):
    p = ROOT / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}


def pick(items, fn, n):
    rows = []
    for i in items:
        if not i.get("active", True) or i.get("dup_of") or i.get("source") == "Вручну":
            continue
        sc = fn(i)
        if sc: rows.append((sc[0], i, sc))
    rows.sort(key=lambda x: -x[0])
    return rows[:n], len(rows)


def main():
    sale = load("docs/data/listings.json"); rent = load("docs/data/rent.json")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sec, sec_n = pick([i for i in sale["items"] if not i.get("flags", {}).get("new_build")], sale_score, A.get("top_sale", 15))
    nb, nb_n = pick([i for i in sale["items"] if i.get("flags", {}).get("new_build")], sale_score, A.get("top_newbuild", 8))
    rn, rn_n = pick(rent["items"], rent_score, A.get("top_rent", 12))
    md = [f"# Шортлист квартир (Львів) — {now}",
          "",
          "Автоматичний відбір для обговорення з Claude. Критерії — `criteria.md` у репозиторії, числа — config.json → advisor.",
          f"Дані: купівля оновлена {sale.get('updated')}, оренда {rent.get('updated')}. Курс ${sale.get('usd_rate')}.",
          f"Повні дані (усі поля, фото): {SITE}/data/listings.json і {SITE}/data/rent.json",
          "Бал — грубий фільтр, не вирок: читай плюси/мінуси й опис.", "",
          f"## Купівля — вторинка (топ {len(sec)} з {sec_n} відповідних)", ""]
    md += [line(i, sc, "sale") for _, i, sc in sec]
    md += [f"## Купівля — новобудови, вже зведені (топ {len(nb)} з {nb_n})", ""]
    md += [line(i, sc, "sale") for _, i, sc in nb]
    md += [f"## Оренда (топ {len(rn)} з {rn_n})", ""]
    md += [line(i, sc, "rent") for _, i, sc in rn]
    out = ROOT / "docs/data/shortlist.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"shortlist: вторинка {sec_n}, новобудови {nb_n}, оренда {rn_n} → {out}")


if __name__ == "__main__":
    sys.exit(main())
