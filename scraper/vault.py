"""Шифрування бази: у репозиторії й на сайті лежать лише *.enc, відкриті файли — тільки локально.

Формат *.enc (JSON): {"v":1,"kdf":"PBKDF2-SHA256","iter":N,"salt":b64,"iv":b64,"ct":b64}
ct = AES-256-GCM(gzip(дані)), ключ = PBKDF2(пароль, salt). Сайт розшифровує те саме через WebCrypto.

Пароль: змінна FS_PASSWORD (у GitHub — секрет репозиторію FS_PASSWORD) або файл .fs_password у корені.

  python scraper/vault.py decrypt     # *.enc → відкриті файли (для збирача й query.py)
  python scraper/vault.py encrypt     # відкриті файли → *.enc (перед комітом)
  python scraper/vault.py check       # чи підходить пароль
"""
import base64, gzip, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Відкриті файли, які в репозиторії зберігаються лише зашифрованими (поруч, з суфіксом .enc).
PRIVATE_FILES = [
    "docs/data/listings.json",
    "docs/data/listings-home.json",
    "docs/data/rent.json",
    "docs/data/shortlist.md",
    "private/criteria.md",
    "private/config.json",
]
ITER = 310_000
# Сіль спільна для всіх файлів: сайт виводить ключ один раз (PBKDF2 повільний навмисно).
SALT_FILE = ROOT / "docs/data/salt.txt"


def password(required=True):
    pw = os.environ.get("FS_PASSWORD") or ""
    f = ROOT / ".fs_password"
    if not pw and f.exists():
        pw = f.read_text(encoding="utf-8").strip()
    if not pw and required:
        sys.exit("Немає пароля: задай FS_PASSWORD (у GitHub — секрет репозиторію) або файл .fs_password")
    return pw


def _salt():
    if not SALT_FILE.exists():
        SALT_FILE.write_text(base64.b64encode(os.urandom(16)).decode() + "\n")
    return base64.b64decode(SALT_FILE.read_text().strip())


_keys = {}


def _key(pw, salt, it):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    k = (pw, salt, it)
    if k not in _keys:
        _keys[k] = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(pw.encode("utf-8"))
    return _keys[k]


def encrypt_bytes(data: bytes, pw: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = _salt(), os.urandom(12)
    ct = AESGCM(_key(pw, salt, ITER)).encrypt(iv, gzip.compress(data, mtime=0), None)
    b = lambda x: base64.b64encode(x).decode()
    return json.dumps({"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b(salt), "iv": b(iv), "ct": b(ct)})


def decrypt_bytes(env_text: str, pw: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    e = json.loads(env_text)
    d = lambda x: base64.b64decode(x)
    try:
        raw = AESGCM(_key(pw, d(e["salt"]), e["iter"])).decrypt(d(e["iv"]), d(e["ct"]), None)
    except Exception:
        sys.exit("Пароль не підходить (FS_PASSWORD / .fs_password)")
    return gzip.decompress(raw)


def decrypt_all(quiet=False):
    pw = password()
    n = 0
    for rel in PRIVATE_FILES:
        enc = ROOT / (rel + ".enc")
        if enc.exists():
            out = ROOT / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(decrypt_bytes(enc.read_text(encoding="utf-8"), pw)); n += 1
    if not quiet: print(f"розшифровано файлів: {n}")


def encrypt_all():
    pw = password()
    n = 0
    for rel in PRIVATE_FILES:
        src, enc = ROOT / rel, ROOT / (rel + ".enc")
        if not src.exists():
            continue
        data = src.read_bytes()
        if enc.exists():
            try:
                if decrypt_bytes(enc.read_text(encoding="utf-8"), pw) == data:
                    continue  # не змінився — не перешифровуємо (інакше новий IV = зайвий коміт)
            except SystemExit:
                pass  # старий пароль — перешифровуємо новим
        enc.write_text(encrypt_bytes(data, pw), encoding="utf-8"); n += 1
    print(f"зашифровано файлів: {n}")


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config():
    """config.json + приватні значення з private/config.json (бюджети, advisor), якщо вони розшифровані."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    p = ROOT / "private/config.json"
    if not p.exists() and (ROOT / "private/config.json.enc").exists() and password(required=False):
        decrypt_all(quiet=True)
    if p.exists():
        cfg = deep_merge(cfg, json.loads(p.read_text(encoding="utf-8")))
    return cfg


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "decrypt": decrypt_all()
    elif cmd == "encrypt": encrypt_all()
    elif cmd == "check":
        enc = next((ROOT / (r + ".enc") for r in PRIVATE_FILES if (ROOT / (r + ".enc")).exists()), None)
        if enc: decrypt_bytes(enc.read_text(encoding="utf-8"), password())
        print("пароль підходить" if enc else "зашифрованих файлів ще немає")
    else: sys.exit(__doc__)
