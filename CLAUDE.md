# FlatSearcher — інструкція для Claude

Сайт моніторингу квартир у Львові (купівля, оренда, новобудови): https://gaspode3.github.io/FlatSearcher/
Дані збирає GitHub Actions (`.github/workflows/scrape.yml`): купівля+оренда щодня о 07:17, оренда ще о 17:17 (Київ).

## База зашифрована

У репозиторії лише `docs/data/*.enc` і `private/*.enc` (AES-GCM, `scraper/vault.py`). Пароль — у документі проєкту
«Пошук квартири» `claude/flatsearcher-api.md` (Projects → project_read). **Ніколи не пиши пароль у файли, що комітяться,
у коміти, issue чи відповіді, якщо користувач не просить.** Локально — лише в `.fs_password` (у .gitignore).

```bash
printf '%s' '<пароль>' > .fs_password
python scraper/vault.py check && python scraper/vault.py decrypt
```
Критерії сім'ї словами — `private/criteria.md`, числами — `private/config.json` → `advisor` (після decrypt). Перед порадами читай criteria.
Змінив щось у `private/` → `python scraper/vault.py encrypt` і комітити лише `*.enc`.

## Підбір варіантів з чату — `scraper/query.py`

Спершу онови дані: `git pull -q --rebase` (query.py сам розшифрує свіжіші *.enc).

```bash
python scraper/query.py stats                                   # коли оновлено, скільки активних, помилки збору
python scraper/query.py search                                  # купівля за критеріями сім'ї, за балом
python scraper/query.py search --max-price 65000 --min-area 60 --old-fund --max-km 3
python scraper/query.py search --mode rent --max-price 650 --rooms 2 --new-days 1
python scraper/query.py search --mode newbuild                  # запасний варіант під єОселю
python scraper/query.py search --text "австрійськ|польськ|сталінк" --all --sort m2
python scraper/query.py show olx-939341144 ria-35100027         # повні картки: усі поля, опис, фото, історія ціни
python scraper/query.py link olx-939341144 ria-35100027 -t "Топ до 80k"  # одне посилання на добірку (#list=…); оренда: --mode rent
```

Фільтри `search`: `--mode sale|rent|newbuild`, `--max-price/--min-price`, `--rooms` (мінімум) / `--rooms-max`,
`--min-area/--max-area`, `--max-m2`, `--max-km` (до пл. Ринок), `--district` (regex), `--text` / `--exclude-text` (regex по опису),
`--old-fund`, `--owner`, `--no-commission`, `--pets`, `--no-panel`, `--livable`, `--gas`, `--min-ceiling 3`,
`--not-first`, `--not-last`, `--max-floor`, `--new-days N`, `--price-drop`, `--with-newbuilds`, `--inactive`,
`--all` (не відкидати за жорсткими умовами advisor: бюджет, площа, хрущовки), `--sort score|price|-price|area|m2|km|new`,
`--limit`, `--format json`. `--remote` — взяти зашифровану базу з сайту (теж потрібен пароль).

Бал — грубий фільтр, не вирок. Для конкретної квартири роби `show` і аналізуй за схемою з інструкцій проєкту.
Посилання на картку на сайті: `https://gaspode3.github.io/FlatSearcher/#id=<id>` (оренда — `#rent&id=<id>`).

## Фото оголошень — `scraper/photos.py`

Сайти оголошень і їхні CDN із чату недоступні, тому фото качає воркфлоу `photos.yml` і кладе зашифрованими
в гілку `photos` (не в main). Одна команда — запустити, дочекатися (~1–2 хв), розшифрувати (потрібен доступ push):

```bash
python scraper/photos.py pull olx-940793524 ria-35100027        # id з бази (+ фото дублікатів з інших сайтів)
python scraper/photos.py pull https://www.olx.ua/d/uk/obyavlenie/…html   # оголошення, якого нема в базі
python scraper/photos.py get olx-940793524                      # уже завантажені — лише розшифрувати (без push)
python scraper/photos.py list                                   # для яких id фото вже є
```
Результат — `/tmp/fs-photos/<id>/NN.jpg` і `sheet.jpg` (усі мініатюри з номерами). Спершу дивись `sheet.jpg`,
потім Read конкретних фото для деталей (вологі плями, вікна, ванна, електрика, димоходи). Гілка тримає 80 останніх оголошень.

## Запустити оновлення бази з чату (потрібен доступ push)

```bash
R=repos/Gaspode3/FlatSearcher/actions
gh api -X POST $R/workflows/scrape.yml/dispatches -f ref=main -f 'inputs[what]=усе'   # або купівля / оренда
sleep 8; gh api "$R/workflows/scrape.yml/runs?per_page=1" --jq '.workflow_runs[0] | "\(.id) \(.status) \(.conclusion) \(.html_url)"'
gh api $R/runs/<run-id> --jq '"\(.status) \(.conclusion)"'    # перевіряти раз на кілька хвилин; потім git pull і query.py
```
У чатах `gh workflow run` / `gh run watch` не працюють (GraphQL закритий) — лише REST через `gh api`.
Секрети репозиторію з чату недоступні. Запуски не накладаються (concurrency: scrape). Оренда швидша за купівлю.
