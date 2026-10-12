# FK + BHM + Proofing + KC + Gantt dashboard: isolated RelaxDev deployment

**Do not change the existing FK bot project.** It must continue using `main`.
The BHM bot is a separate repository `HR-analyze/FK_BHM`.
The proofing/baking bot is a separate repository `HR-analyze/rastoyka_fk` with its own PostgreSQL.
The confectionery (KC) bot is a separate repository `HR-analyze/fk_conditerka` with its own PostgreSQL.

**Never merge `relax-migration` into `main` and never open a PR into `main`:** `main` builds the FK Telegram bot.
This `relax-migration` branch builds **only the combined dashboard** from the root Dockerfile; no Telegram polling or report scheduler starts.

## Create a SECOND RelaxDev service

- GitHub repository: `HR-analyze/fk_analiz`
- Branch: `relax-migration`
- Build: **Dockerfile** from repository root (do not select `dashboard/Dockerfile`, do not set root directory to `dashboard`)
- Container port: `8080`
- One replica is sufficient.

## Environment

```dotenv
PORT=8080
SOURCE_MODE=api
BOT_API_URL=https://<fk-bot-host>
BOT_API_KEY=<same-secret-as-DASHBOARD_API_KEY-on-FK-bot>

BHM_SOURCE_MODE=api
BHM_API_URL=https://<bhm-bot-host>
BHM_API_KEY=<same-secret-as-DASHBOARD_API_KEY-on-BHM-bot>

PROOFING_SOURCE_MODE=api
PROOFING_API_URL=https://<rastoyka-bot-host>
PROOFING_API_KEY=<same-secret-as-DASHBOARD_API_KEY-on-rastoyka-bot>

KC_SOURCE_MODE=api
KC_API_URL=https://<konditerka-bot-host>
KC_API_KEY=<same-secret-as-DASHBOARD_API_KEY-on-konditerka-bot>

PLAN_UPLOAD_TOKEN=<long-random-secret>
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<long-unique-password>

TIMEZONE=Europe/Moscow
CACHE_TTL=60
REFRESH_SECONDS=60
```

**Attach a NEW PostgreSQL to the NEW dashboard project in RelaxDev.** RelaxDev automatically sets `DATABASE_URL`; **do not copy or enter this variable manually**. The dashboard reads this automatically only when `RELAXDEV_DEPLOY=1`, which is baked into the Dockerfile. Never attach a bot's PostgreSQL to the dashboard: this database must belong exclusively to the dashboard project. It stores production plans and upload history in `fk_dashboard_plan_state`. You may optionally set `PLAN_DATABASE_URL` as an explicit override for external plan storage, but it is not required for the standard RelaxDev flow.

`BOT_API_URL`, `BHM_API_URL`, `PROOFING_API_URL` and `KC_API_URL` are the publicly reachable service origins, with no `/api/dashboard/all` suffix. The API keys are server-side only, and must be set in the respective bot project as `DASHBOARD_API_KEY`. For the proofing bot use its `DASHBOARD_API_KEY`, **not** `ANALYTICS_KEY` (that one is for people in the Mini App). The dashboard does not start any bot.

The proofing section needs **no database of its own**: it reads the proofing bot API only and keeps a short in-memory cache (`CACHE_TTL`). Photos are fetched one by one when a user opens a batch gallery, through `/api/proofing/photo/{id}` of the dashboard; the bot key never reaches the browser. If `PROOFING_API_URL` is empty, the «Расстойка» button is disabled; if it is set, `PROOFING_API_KEY` is mandatory (startup fails otherwise).

The KC section works the same way: API only (no access to the KC bot database, no new dashboard tables), key server-side only, in-memory cache. Empty `KC_API_URL` disables the «КЦ» button; `KC_API_URL` without `KC_API_KEY` fails startup. KC «processed pieces» are a sum over operations (one item passing three operations is counted three times) and are never presented as finished output.

The «Гант» section needs no new variables: it combines the four sources above plus the one-time parsed KC Telegram chat (`kc_chat_history.json`, baked into the image). Its mapping (shop product names → unified product, hand-typed KC operations → unified operation) is uploaded on the site with `PLAN_UPLOAD_TOKEN` and stored in the same dashboard PostgreSQL in a **separate** table `fk_dashboard_product_map`, created automatically on startup; the plans table is not touched. Packing (фасовка) has no source yet — it will be a separate bot. KC data taken from the chat is marked as inaccurate on the page and in the exports.

Missing mandatory settings make startup fail instead of silently exposing data or writing transient plans. A username/password challenge protects the dashboard endpoints; `/health` remains publicly accessible to platform probes.

## Verify

1. Open `/health`; confirm `ok: true` and `mode: api`.
2. Open `/`; enter dashboard login/password; confirm FK facts, BHM tab and exports.
3. Click «Расстойка»; confirm KPI, charts, «Партии» tab, a photo gallery, Excel and PDF. `/health` shows `proofing.error` empty.
4. Click «КЦ»; confirm «Сводка», «Операции», «Нормативы», Excel and PDF. `/health` shows `kc.error` empty.
5. Click «Гант»; pick a day, check the chart (product rows collapse; columns equipment / people / output per person-hour), «Общий тайминг», «Среднее по продукту» (expand a product), download the template, upload it back.
6. Upload a TEST plan with `PLAN_UPLOAD_TOKEN`. Visit `/api/plan` and `/api/plan/history`.
7. Deploy the same commit again; confirm uploaded plans and history persist.

## Existing plans on inaccessible VM

The original dashboard saves plans in a local `/app/data/plans.json` on the old VM. **Deploying this new dashboard does not transfer them.** Restore or export that JSON after the old VM recovers, then migrate it deliberately to the new dashboard database; never overwrite production plans blindly. Existing bot production history likewise stays in its original bot database until restored.

## How to diagnose errors

- Missing `DATABASE_URL`: create and attach the separate PostgreSQL to the *new dashboard* project. RelaxDev populates `DATABASE_URL` automatically.
- `/health` 503: check FK bot URL/key, and PostgreSQL connectivity. Dashboard will not serve facts before those are available.
- BHM tab unavailable: check BHM URL/key and running BHM bot. FK and BHM bots stay independent.
- «Расстойка» shows «API расстойки недоступно»: check `PROOFING_API_URL`/`PROOFING_API_KEY` and that the rastoyka bot is running (`curl -H "X-API-Key: …" $PROOFING_API_URL/api/dashboard/all`). FK and BHM keep working; `/health` stays 200.
- «КЦ» shows «API кондитерского цеха недоступно»: check `KC_API_URL`/`KC_API_KEY` (the KC bot's `DASHBOARD_API_KEY`, not `ANALYTICS_KEY`) and that the KC bot is running. Other sections keep working.
- Error on plan upload: check `PLAN_UPLOAD_TOKEN`; uploads are authenticated separately from viewing.
