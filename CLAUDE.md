# FlatSearcher — інструкція для Claude

Сайт моніторингу квартир у Львові (купівля, оренда, новобудови): https://gaspode3.github.io/FlatSearcher/
Дані збирає GitHub Actions (`.github/workflows/scrape.yml`): купівля+оренда щодня о 07:17, оренда ще о 17:17 (Київ).
Критерії сім'ї словами — `criteria.md`, числами — `config.json` → `advisor`. Перед порадами читай `criteria.md`.

## Підбір варіантів з чату — `scraper/query.py`

Спершу онови дані: `git pull -q` (бот комітить нову базу після кожного збору).

```bash
python scraper/query.py stats                                   # коли оновлено, скільки активних, помилки збору
python scraper/query.py search                                  # купівля за критеріями сім'ї, за балом
python scraper/query.py search --max-price 65000 --min-area 60 --old-fund --max-km 3
python scraper/query.py search --mode rent --max-price 650 --rooms 2 --new-days 1
python scraper/query.py search --mode newbuild                  # запасний варіант під єОселю
python scraper/query.py search --text "австрійськ|польськ|сталінк" --all --sort m2
python scraper/query.py show olx-939341144 ria-35100027         # повні картки: усі поля, опис, фото, історія ціни
```

Фільтри `search`: `--mode sale|rent|newbuild`, `--max-price/--min-price`, `--rooms` (мінімум) / `--rooms-max`,
`--min-area/--max-area`, `--max-m2`, `--max-km` (до пл. Ринок), `--district` (regex), `--text` / `--exclude-text` (regex по опису),
`--old-fund`, `--owner`, `--no-commission`, `--pets`, `--no-panel`, `--livable`, `--gas`, `--min-ceiling 3`,
`--not-first`, `--not-last`, `--max-floor`, `--new-days N`, `--price-drop`, `--with-newbuilds`, `--inactive`,
`--all` (не відкидати за жорсткими умовами advisor: бюджет, площа, хрущовки), `--sort score|price|-price|area|m2|km|new`,
`--limit`, `--format json`. Без клону: `--remote` бере JSON із сайту.

Бал — грубий фільтр, не вирок. Для конкретної квартири роби `show` і аналізуй за схемою з інструкцій проєкту.
Посилання на картку на сайті: `https://gaspode3.github.io/FlatSearcher/#id=<id>` (оренда — `#rent&id=<id>`).

## Запустити оновлення бази з чату

```bash
R=repos/Gaspode3/FlatSearcher/actions
gh api -X POST $R/workflows/scrape.yml/dispatches -f ref=main -f 'inputs[what]=усе'   # або купівля / оренда
sleep 8; gh api "$R/workflows/scrape.yml/runs?per_page=1" --jq '.workflow_runs[0] | "\(.id) \(.status) \(.conclusion) \(.html_url)"'
gh api $R/runs/<run-id> --jq '"\(.status) \(.conclusion)"'    # перевіряти раз на кілька хвилин; потім git pull і query.py
```
У чатах `gh workflow run` / `gh run watch` не працюють (GraphQL закритий) — лише REST через `gh api`.
Запуски не накладаються (concurrency: scrape). Оренда швидша за купівлю.

## Без репозиторію (лише веб)
Компактні списки, що оновлюються з кожним збором: https://gaspode3.github.io/FlatSearcher/api/index.md
(`api/sale.md`, `api/rent.md`, `api/newbuild.md`) і шортлист `data/shortlist.md`.
