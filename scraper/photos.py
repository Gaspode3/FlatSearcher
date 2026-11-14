"""Фото оголошень для Claude: GitHub Actions завантажує фото, у чаті їх можна розшифрувати й роздивитись.

Навіщо: із чату сайти оголошень і їхні CDN недоступні, а GitHub — доступний. Тому фото качає воркфлоу
`.github/workflows/photos.yml` і кладе їх зашифрованими (тим самим паролем, що й база) у гілку `photos`,
щоб не роздувати main і сайт. Формат: photos/<id>/NN.jpg.enc (JPEG ≤1600 px, AES-GCM як у vault.py).

У чаті (одна команда: запускає воркфлоу, чекає ~1–2 хв, розшифровує):
  python scraper/photos.py pull olx-940793524 ria-35100027
  python scraper/photos.py pull https://www.olx.ua/d/uk/obyavlenie/...html   # і оголошення, яких нема в базі
  python scraper/photos.py get olx-940793524        # лише розшифрувати те, що вже є в гілці photos
  python scraper/photos.py list                     # для яких id фото вже завантажені

Результат — /tmp/fs-photos/<id>/NN.jpg (+ sheet.jpg — усі фото на одному аркуші для швидкого огляду).

У воркфлоу (не з чату):
  python scraper/photos.py fetch <id або URL> ... --out photos-out
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import vault  # noqa: E402

BRANCH = "photos"
REPO = "Gaspode3/FlatSearcher"
WORKFLOW = "photos.yml"
DATA = ["docs/data/listings.json", "docs/data/rent.json", "docs/data/listings-home.json"]
OUT_LOCAL = Path("/tmp/fs-photos")
MAX_SIDE = 1600


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- база

def load_items() -> dict:
    items = {}
    for rel in DATA:
        p = ROOT / rel
        if not p.exists() and (ROOT / (rel + ".enc")).exists():
            vault.decrypt_all(quiet=True)
        if p.exists():
            for it in json.loads(p.read_text(encoding="utf-8")).get("items") or []:
                items.setdefault(it["id"], it)
    return items


def key_for(target: str, items: dict) -> str:
    """id з бази, або id оголошення, якого шукали за URL, або url-<hash>."""
    if not target.startswith("http"):
        return target
    norm = target.split("?")[0].split("#")[0]
    same = lambda u: bool(u) and u.split("?")[0].split("#")[0] == norm  # noqa: E731
    for it in items.values():  # спершу власне посилання оголошення, потім дублікати на інших сайтах
        if same(it.get("url")):
            return it["id"]
    for it in items.values():
        if any(same(d.get("url")) for d in it.get("also_on") or []):
            return it["id"]
    return "url-" + hashlib.md5(norm.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- завантаження (у GitHub Actions)

IMG_RES = [
    re.compile(r"https://[a-z]+\.apollo\.olxcdn\.com(?::443)?/v1/files/[A-Za-z0-9_-]+-UA/image"),
    re.compile(r"https://market-images\.lunstatic\.net/lun-ua/\d+/\d+/images/offers/\d+\.jpg"),
    re.compile(r"https://(?:www\.)?mistechko\.com/[^\"'\s]+?\.(?:jpe?g|webp)"),
]


def page_photos(url: str) -> list[str]:
    import scrape  # noqa: WPS433  (requests/bs4 потрібні лише тут)
    html = scrape.get(url) or ""
    html = html.replace("\\u002F", "/").replace("\\/", "/")
    out: list[str] = []
    if "dom.ria.com" in url:
        out = scrape.ria_photos_from_html(html, scrape.ria_slug(url))
    for rx in IMG_RES:
        for u in rx.findall(html):
            if "olxcdn" in u:
                u += ";s=1600x1200"
            elif "lunstatic" in u:
                u = re.sub(r"/lun-ua/\d+/\d+/", "/lun-ua/1140/721/", u)
            if u not in out:
                out.append(u)
    return out


def urls_for(target: str, items: dict) -> list[str]:
    key = key_for(target, items)
    it = items.get(key)
    if not it:
        if not target.startswith("http"):
            log(f"  ! {target}: нема в базі — дай посилання на оголошення")
            return []
        return page_photos(target)
    urls = list(it.get("photos") or [])
    # дублікати того самого оголошення на інших сайтах часто мають інші/додаткові фото
    for d in it.get("also_on") or []:
        dup = items.get(d.get("id"))
        urls += (dup.get("photos") if dup else None) or []
    if len(urls) < 6 and it.get("url"):
        urls += page_photos(it["url"])  # у базі може бути лише кілька фото зі списку — беремо зі сторінки
    seen, out = set(), []
    for u in urls:
        u = re.sub(r";s=\d+x\d+", ";s=1600x1200", u)
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def to_jpeg(raw: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


def dhash(jpg: bytes) -> int:
    from PIL import Image
    im = Image.open(io.BytesIO(jpg)).convert("L").resize((9, 8))
    px = list(im.getdata())
    return sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])


def cmd_fetch(a):
    import requests
    from scrape import HEADERS
    pw = vault.password()
    items = load_items()
    out = Path(a.out)
    summary = []
    for target in a.targets:
        key = key_for(target, items)
        urls = urls_for(target, items)[: a.max]
        d = out / "photos" / key
        if d.exists():
            for f in d.glob("*"):
                f.unlink()
        d.mkdir(parents=True, exist_ok=True)
        n, hashes = 0, []
        for u in urls:
            try:
                r = requests.get(u, headers={**HEADERS, "Referer": "https://www.olx.ua/"}, timeout=30)
                r.raise_for_status()
                jpg = to_jpeg(r.content)
            except Exception as e:  # noqa: BLE001
                log(f"  ! {u}: {e}")
                continue
            h = dhash(jpg)
            if any(bin(h ^ x).count("1") <= 12 for x in hashes):
                continue  # те саме фото з дубліката на іншому сайті
            hashes.append(h)
            n += 1
            (d / f"{n:02d}.jpg.enc").write_text(vault.encrypt_bytes(jpg, pw), encoding="utf-8")
        meta = {"id": key, "source": target, "count": n, "found": len(urls), "fetched": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"}
        (d / "meta.json.enc").write_text(vault.encrypt_bytes(json.dumps(meta, ensure_ascii=False).encode(), pw), encoding="utf-8")
        summary.append(f"{key}: {n}/{len(urls)}")
        log(f"{key}: збережено {n} з {len(urls)}")
    print("; ".join(summary))


# ---------------------------------------------------------------- у чаті

def git(*args, check=True) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=check, capture_output=True, text=True).stdout


def fetch_branch():
    # --filter=blob:none: тягнемо лише дерево, а самі фото — по запиту в git show (гілка росте)
    git("fetch", "-q", "--filter=blob:none", "--depth", "1", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")


def remote_files(key: str | None = None) -> list[str]:
    path = f"photos/{key}/" if key else "photos/"
    return [f for f in git("ls-tree", "-r", "--name-only", f"origin/{BRANCH}", "--", path, check=False).splitlines() if f]


def contact_sheet(files: list[Path], dest: Path):
    from PIL import Image, ImageDraw
    cols, w = 4, 400
    thumbs = []
    for f in files:
        im = Image.open(f); im.thumbnail((w, w)); thumbs.append(im)
    if not thumbs:
        return
    h = max(t.height for t in thumbs)
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), "white")
    dr = ImageDraw.Draw(sheet)
    for i, t in enumerate(thumbs):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(t, (x, y))
        dr.rectangle([x, y, x + 34, y + 22], fill="black"); dr.text((x + 6, y + 5), f"{i + 1:02d}", fill="white")
    sheet.save(dest, "JPEG", quality=80)


def cmd_get(a, fetched=False):
    if not fetched:
        fetch_branch()
    pw = vault.password()
    items = load_items()
    for target in a.targets:
        key = key_for(target, items)
        files = [f for f in remote_files(key) if f.endswith(".jpg.enc")]
        if not files:
            print(f"{key}: фото ще не завантажені — python scraper/photos.py pull {target}")
            continue
        d = OUT_LOCAL / key
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.jpg"):
            old.unlink()
        out = []
        for f in sorted(files):
            env = git("show", f"origin/{BRANCH}:{f}")
            p = d / Path(f).name.replace(".enc", "")
            p.write_bytes(vault.decrypt_bytes(env, pw)); out.append(p)
        contact_sheet(out, d / "sheet.jpg")
        meta = git("show", f"origin/{BRANCH}:photos/{key}/meta.json.enc", check=False)
        m = json.loads(vault.decrypt_bytes(meta, pw)) if meta else {}
        print(f"{key}: {len(out)} фото (знайдено {m.get('found', '?')}, завантажено {m.get('fetched', '?')}) → {d}/  аркуш: {d}/sheet.jpg")


def gh(*args) -> str:
    return subprocess.run(["gh", "api", *args], check=True, capture_output=True, text=True).stdout


def cmd_pull(a):
    R = f"repos/{REPO}/actions"
    started = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=5)
    gh("-X", "POST", f"{R}/workflows/{WORKFLOW}/dispatches", "-f", "ref=main", "-f", f"inputs[targets]={' '.join(a.targets)}")
    log("воркфлоу запущено, чекаю…")
    run = None
    for _ in range(a.timeout // 10):
        time.sleep(10)
        runs = json.loads(gh(f"{R}/workflows/{WORKFLOW}/runs?per_page=5&event=workflow_dispatch"))["workflow_runs"]
        mine = [r for r in runs if dt.datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ") >= started]
        if mine:
            run = mine[-1]
            if run["status"] == "completed":
                break
    if not run or run["status"] != "completed":
        sys.exit(f"не дочекався (run: {run and run['html_url']}); пізніше: python scraper/photos.py get {' '.join(a.targets)}")
    if run["conclusion"] != "success":
        sys.exit(f"воркфлоу впав: {run['html_url']}")
    cmd_get(a)


def cmd_list(a):
    fetch_branch()
    ids = sorted({f.split("/")[1] for f in remote_files()})
    print("\n".join(ids) if ids else "гілка photos порожня")


def cmd_prune(a):
    """У воркфлоу: лишити в гілці photos лише --keep найсвіжіших оголошень."""
    pw = vault.password()
    base = Path(a.dir) / "photos"
    dirs = []
    for d in base.glob("*/"):
        m = d / "meta.json.enc"
        when = json.loads(vault.decrypt_bytes(m.read_text(encoding="utf-8"), pw)).get("fetched", "") if m.exists() else ""
        dirs.append((when, d))
    for _, d in sorted(dirs, reverse=True)[a.keep:]:
        for f in d.glob("*"):
            f.unlink()
        d.rmdir(); log(f"видалено старі фото: {d.name}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("targets", nargs="+"); f.add_argument("--out", default="photos-out"); f.add_argument("--max", type=int, default=30)
    g = sub.add_parser("get"); g.add_argument("targets", nargs="+")
    u = sub.add_parser("pull"); u.add_argument("targets", nargs="+"); u.add_argument("--timeout", type=int, default=600)
    sub.add_parser("list")
    r = sub.add_parser("prune"); r.add_argument("dir"); r.add_argument("--keep", type=int, default=80)
    a = p.parse_args()
    {"fetch": cmd_fetch, "get": cmd_get, "pull": cmd_pull, "list": cmd_list, "prune": cmd_prune}[a.cmd](a)


if __name__ == "__main__":
    main()
