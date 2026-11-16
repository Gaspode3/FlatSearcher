"""Спільні коментарі до квартир: docs/data/notes.json.enc (зашифровано тим самим паролем, що й база).

Пишуть і сайт (через GitHub API з токеном), і чат з Claude (цей скрипт: коміт + push у main).
Формат відкритих даних: {"v": 1, "notes": {"<id оголошення>": [{"id", "at", "by", "text"}, ...]}}

  python scraper/notes.py list                     # усі квартири з коментарями
  python scraper/notes.py list olx-939341144       # коментарі до квартири
  python scraper/notes.py add olx-939341144 "Подзвонив: опалення — котел Vaillant 2019" [--by Claude]
  python scraper/notes.py rm olx-939341144 <id коментаря>

add / rm потребують доступу push (оновлюють main і пушать; при конфлікті повторюють).
"""
import argparse, json, secrets, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault  # noqa: E402

ROOT = vault.ROOT
REL = "docs/data/notes.json.enc"
ENC = ROOT / REL


def load() -> dict:
    if not ENC.exists():
        return {"v": 1, "notes": {}}
    d = json.loads(vault.decrypt_bytes(ENC.read_text(encoding="utf-8"), vault.password()))
    d.setdefault("notes", {})
    return d


def for_id(id_: str) -> list:
    """Коментарі до оголошення (для query.py); без пароля чи файлу — порожньо."""
    try:
        return load()["notes"].get(id_, []) if ENC.exists() and vault.password(required=False) else []
    except SystemExit:
        return []


def counts() -> dict:
    try:
        return {k: len(v) for k, v in load()["notes"].items() if v} if ENC.exists() and vault.password(required=False) else {}
    except SystemExit:
        return {}


def save(d: dict):
    ENC.write_text(vault.encrypt_bytes(json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8"), vault.password()), encoding="utf-8")


def git(*a, check=True):
    return subprocess.run(["git", "-C", str(ROOT), *a], check=check, capture_output=True, text=True)


def commit_with(mutate, message):
    """Оновити main, змінити нотатки, закомітити й запушити; при гонці з іншим записом — повторити."""
    for attempt in range(4):
        git("pull", "-q", "--rebase")
        d = load()
        result = mutate(d)
        save(d)
        git("add", REL)
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return result
        git("commit", "-q", "-m", message)
        if git("-c", "push.negotiate=true", "push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            return result
        git("reset", "-q", "--hard", "HEAD~1")  # хтось записав раніше — беремо свіже й повторюємо
    sys.exit("Не вдалося запушити коментар (немає доступу push?)")


def fmt(n):
    at = datetime.fromisoformat(n["at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y %H:%M")
    return f"- [{n['id']}] {at} · {n.get('by') or '?'}: {n['text']}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    ls = sp.add_parser("list"); ls.add_argument("ids", nargs="*")
    ad = sp.add_parser("add"); ad.add_argument("id"); ad.add_argument("text"); ad.add_argument("--by", default="Claude")
    rm = sp.add_parser("rm"); rm.add_argument("id"); rm.add_argument("note_id")
    a = p.parse_args()

    if a.cmd == "list":
        git("pull", "-q", "--rebase", check=False)
        notes = load()["notes"]
        for id_ in a.ids or sorted(notes, key=lambda k: max(n["at"] for n in notes[k]), reverse=True):
            items = notes.get(id_, [])
            print(f"## {id_} ({len(items)})")
            for n in items:
                print(fmt(n))
            print()
        if not notes and not a.ids:
            print("Коментарів ще немає.")
    elif a.cmd == "add":
        n = {"id": secrets.token_hex(4), "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
             "by": a.by, "text": a.text.strip()}
        commit_with(lambda d: d["notes"].setdefault(a.id, []).append(n), f"Коментар до {a.id}")
        print("додано: " + fmt(n))
    elif a.cmd == "rm":
        def drop(d):
            items = d["notes"].get(a.id, [])
            left = [n for n in items if n["id"] != a.note_id]
            if len(left) == len(items):
                sys.exit(f"Коментаря {a.note_id} у {a.id} немає")
            if left: d["notes"][a.id] = left
            else: d["notes"].pop(a.id, None)
        commit_with(drop, f"Видалено коментар до {a.id}")
        print("видалено")


if __name__ == "__main__":
    main()
