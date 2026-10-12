# FK + BHM dashboard: isolated RelaxDev deployment

**Do not change the existing FK bot project.** It must continue using `main`.
The BHM bot is a separate repository `HR-analyze/FK_BHM`.
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

PLAN_DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<dashboard-only-database>
PLAN_UPLOAD_TOKEN=<long-random-secret>
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<long-unique-password>

TIMEZONE=Europe/Moscow
CACHE_TTL=60
REFRESH_SECONDS=60
```

**Create a NEW PostgreSQL instance/database for `PLAN_DATABASE_URL`.** Never point it at the FK bot or the BHM bot database. This new database stores *only* dashboard plan data and the upload history in table `fk_dashboard_plan_state`.

`BOT_API_URL` and `BHM_API_URL` are the publicly reachable service origins, with no `/api/dashboard/all` suffix. The API keys are server-side only, and must be set in the respective bot project as `DASHBOARD_API_KEY`. The dashboard does not start either bot.

Missing mandatory settings make startup fail instead of silently exposing data or writing transient plans. A username/password challenge protects the dashboard endpoints; `/health` remains publicly accessible to platform probes.

## Verify

1. Open `/health`; confirm `ok: true` and `mode: api`.
2. Open `/`; enter dashboard login/password; confirm FK facts, BHM tab and exports.
3. Upload a TEST plan with `PLAN_UPLOAD_TOKEN`. Visit `/api/plan` and `/api/plan/history`.
4. Deploy the same commit again; confirm uploaded plans and history persist.

## Existing plans on inaccessible VM

The original dashboard saves plans in a local `/app/data/plans.json` on the old VM. **Deploying this new dashboard does not transfer them.** Restore or export that JSON after the old VM recovers, then migrate it deliberately to the new dashboard database; never overwrite production plans blindly. Existing bot production history likewise stays in its original bot database until restored.

## How to diagnose errors

- Missing `PLAN_DATABASE_URL`: create and attach the separate database to the *new dashboard* project.
- `/health` 503: check FK bot URL/key, and PostgreSQL connectivity. Dashboard will not serve facts before those are available.
- BHM tab unavailable: check BHM URL/key and running BHM bot. FK and BHM bots stay independent.
- Error on plan upload: check `PLAN_UPLOAD_TOKEN`; uploads are authenticated separately from viewing.
