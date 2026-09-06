# ФК — дашборд производства

Отдельный веб-сервис, который показывает аналитику по данным бота ФК.
Живёт на вашей ВМ (виртуальная машина), крутится в Docker, наружу смотрит через nginx.

## Главное про безопасность данных

Сервис **не пишет в базу и не может этого сделать**. Три уровня защиты:

1. Соединение открывается с `default_transaction_read_only=on` — PostgreSQL отбивает
   любой `INSERT/UPDATE/DELETE/CREATE/DROP/TRUNCATE` ошибкой `25006`.
2. В коде стоит guard `_assert_read_only()` — выполняются только `SELECT` и `WITH`.
3. В сервисе физически нет ни одного SQL-запроса на запись, нет миграций,
   нет `CREATE TABLE IF NOT EXISTS`. Только два `SELECT` (см. `sources.py`).

Плюс `statement_timeout` (по умолчанию 15 с), чтобы тяжёлый запрос не подвесил базу,
и кеш на стороне сервиса — в базу ходим раз в `CACHE_TTL` секунд независимо от
числа открытых вкладок.

Бот (`bot.py` в корне репозитория) не изменён ни на строку.

## Режимы источника данных (`SOURCE_MODE`)

| Режим  | Что делает | Когда нужен |
|--------|-----------|-------------|
| `db`   | Прямой коннект к PostgreSQL, только чтение | ВМ видит базу по сети (основной вариант) |
| `api`  | Тянет готовый `/api/dashboard/all` бота по ключу | База закрыта, но HTTP бота доступен |
| `demo` | Локальный `demo_data.json`, к базе не обращается | Проверить деплой и nginx, не трогая прод |

## Деплой на ВМ

### 0. Что должно быть на машине

```bash
docker --version && docker compose version   # Docker и плагин compose
nginx -v                                     # nginx
```

Если Docker не стоит (Ubuntu/Debian):

```bash
curl -fsSL https://get.docker.com | sh
sudo apt-get install -y nginx
```

### 1. Забрать код

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/HR-analyze/fk_analiz.git
cd /opt/fk_analiz/dashboard
```

### 2. Настроить `.env`

```bash
cp .env.example .env
nano .env
```

Минимум, что надо заполнить для боевого режима:

```ini
SOURCE_MODE=db
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
TIMEZONE=Europe/Moscow
CACHE_TTL=60
REFRESH_SECONDS=60
```

`DATABASE_URL` — та же строка подключения, что у бота на Relax Dev.
Если панель отдаёт хост/порт/юзера по отдельности, строка собирается так:
`postgresql://<user>:<password>@<host>:<port>/<database>?sslmode=require`.

> Если база на Relax Dev слушает только внутреннюю сеть — прямого коннекта с ВМ не будет.
> Тогда переключись на `SOURCE_MODE=api`, укажи `BOT_API_URL` и `BOT_API_KEY`
> (тот же `DASHBOARD_API_KEY`, что задан у бота).

### 3. Сначала прогнать на демо-данных

Чтобы убедиться, что контейнер и nginx живые, ещё не подключаясь к боевой базе:

```bash
SOURCE_MODE=demo docker compose up -d --build
curl -s localhost:8090/health
```

Ожидаем `{"ok": true, "mode": "demo", ...}`.

### 4. Переключить на боевые данные

```bash
docker compose down
docker compose up -d --build        # теперь читает SOURCE_MODE из .env
docker compose logs -f --tail=50
```

В логах должно быть:
`snapshot refreshed: production=… pauses=… mode=db`

### 5. nginx

```bash
sudo cp nginx/rate-limit.conf /etc/nginx/conf.d/fk-dashboard-rate-limit.conf
sudo cp nginx/fk-dashboard.conf /etc/nginx/sites-available/fk-dashboard.conf
sudo nano /etc/nginx/sites-available/fk-dashboard.conf   # подставить свой server_name
sudo ln -s /etc/nginx/sites-available/fk-dashboard.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Проверка: `curl -I http://<домен-или-IP>/` → `200 OK`.

### 6. HTTPS (если есть домен)

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d dashboard.example.ru
```

### 7. Файрвол

Наружу нужны только 80/443. Порт 8090 слушает **только localhost** (см. `docker-compose.yml`),
поэтому мимо nginx к сервису не достучаться.

```bash
sudo ufw allow 80,443/tcp && sudo ufw enable
```

## Обновление

```bash
cd /opt/fk_analiz && git pull
cd dashboard && docker compose up -d --build
```

## Что показывает дашборд

Фильтры: период (даты от/до), оборудование, продукт, смена (день 08:00–20:00 / ночь 20:00–08:00).

- 6 KPI: операций, выпуск (шт), средняя длительность, средняя производительность,
  число простоев, коэффициент использования = работа / (работа + простои)
- Выпуск по дням, производительность по часам (штуки распределяются по минутам операции)
- Выпуск по оборудованию, топ продуктов
- Простои по причинам (круговая), работа vs простои (кольцевая)
- Сводка по дням, сводка по оборудованию, тайминги по продуктам, детализация операций

Страница сама обновляется каждые `REFRESH_SECONDS` секунд, в базу при этом ходит
не чаще `CACHE_TTL`. Графики нарисованы на чистом SVG — внешние CDN не нужны,
дашборд работает и в закрытом контуре без интернета.

## API сервиса

| Метод | Назначение |
|-------|-----------|
| `GET /` | сама страница |
| `GET /health` | 200 пока сервис может отдать данные, 503 если снимка нет |
| `GET /api/meta` | справочники, границы дат, режим источника |
| `GET /api/summary?date_from=&date_to=&equipment=&product=&shift=&refresh=1` | все показатели |

## Если что-то не так

| Симптом | Куда смотреть |
|---------|--------------|
| `docker compose logs` пишет `DATABASE_URL is not set` | не заполнен `.env` или он не подхватился (`env_file: .env`) |
| `OperationalError: connection refused` / таймаут | ВМ не видит базу: файрвол на стороне Relax Dev, неверный хост/порт → пробуй `SOURCE_MODE=api` |
| `password authentication failed` | неверный логин/пароль в `DATABASE_URL` |
| `bot API rejected the key (401)` | `BOT_API_KEY` не совпадает с `DASHBOARD_API_KEY` бота |
| В логе `startup options rejected by the server` | нормально: база за пулером, сервис сам перешёл на `SET`, read-only сохранён |
| Страница есть, но пусто и жёлтый баннер | проверь `curl localhost:8090/health` на ВМ |
| 502 от nginx | контейнер не поднялся: `docker compose ps`, `docker compose logs` |

## Закрыть доступ, если публичность передумаешь

Дашборд по умолчанию открыт всем, у кого есть ссылка. Закрыть — двумя строками в nginx
(закомментированные блоки лежат прямо в `nginx/fk-dashboard.conf`):

```bash
# по паролю
sudo apt-get install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd fk
# добавить в server{}:  auth_basic "FK dashboard";  auth_basic_user_file /etc/nginx/.htpasswd;
sudo nginx -t && sudo systemctl reload nginx
```
