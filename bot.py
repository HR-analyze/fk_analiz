import asyncio,hashlib,hmac,json,logging,os,sqlite3,uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo
import psycopg
from psycopg.rows import dict_row
from aiohttp import web
from aiogram import Bot,Dispatcher
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup,Message
logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv("BOT_TOKEN","").strip();TZ=ZoneInfo(os.getenv("TIMEZONE","Europe/Moscow"));CHAT=os.getenv("WORK_CHAT_ID","").strip();PORT=int(os.getenv("PORT","8080"));DATABASE_URL=os.getenv("DATABASE_URL","").strip();DB=Path(os.getenv("DB_PATH","/app/data/fk.db"));DB.parent.mkdir(parents=True,exist_ok=True);DASHBOARD_API_KEY=os.getenv("DASHBOARD_API_KEY","").strip()
# Production reference: equipment -> products. Forming type is deliberately not collected.
PRODUCTS={
"König Combi Linie Plus":["Багет злаковый","Багет молочный","Багет сырный","Булочка для гамбургера без кунжута","Булочка для френч-дога","Булочка для хот-дога белая"],
"König Ceres":["Багет французский","Багет ремесленный","Хлеб бездрожжевой с семечками","Хлеб гречишный","Хлеб картофельный","Хлеб кефирный","Хлеб протеиновый","Хлеб пшеничный домашний","Хлеб с семенами чиа и пажитником","Хлеб тартин ржано-пшеничный","Хлеб тартин розовый"],
"Glimek":["Батон классический","Лепёшка сдобная","Лепёшка сдобная с сосиской","Мини-хала","Хлеб тостовый слоёный фреш"],
"RONDO Cromaster":["Булочка бриошь зерновая","Круассан для сэндвича","Круассан французский","Круассан французский с сыром"],
"RONDO Smartline":["Булка Много мака","Булочка Сладкое сердце","Краюшки зерновые","Мини-чиабатта","Пирожок с капустой фреш","Сочник с творогом","Чиабатта пшеничная смесевая"],
"RONDO Starline":["Венгерская ватрушка","Основа для слойки с вишней","Слойка голландская","Слойка с марсельской сосиской","Улитка с изюмом","Улитка с маком"],
"TRIMA":["Булочка для супа тёмная","Булочка для Тануки с кунжутом","Булочка суповая светлая"],
"Варочный котёл CHEF 60LCD + миксер STARMIX":["Трубочка"],
"Diosna":["Начинка маковая для улитки"],
"Ротационная печь":["Улитка с маком 110 г"],
"MIWE GR":["Круассан классика мини 55 г неотпеч"]}
EQUIPMENT=list(PRODUCTS.keys())
REASONS=["Поломка оборудования","Нет сырья","Нет персонала","Техническая проблема","Качество продукции","Нет инвентаря"]
def db():
 if not DATABASE_URL:raise RuntimeError("DATABASE_URL is not set")
 return psycopg.connect(DATABASE_URL,row_factory=dict_row)
def init_db():
 with db() as c:
  c.execute("CREATE TABLE IF NOT EXISTS production (id TEXT PRIMARY KEY,user_id BIGINT NOT NULL,user_name TEXT,equipment TEXT,forming TEXT,product TEXT,start_time TEXT,end_time TEXT,quantity INTEGER,status TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS pauses (id TEXT PRIMARY KEY,user_id BIGINT NOT NULL,user_name TEXT,equipment TEXT,reason TEXT,start_time TEXT,end_time TEXT,created_at TIMESTAMPTZ NOT NULL)")
  c.execute("CREATE INDEX IF NOT EXISTS idx_production_user_status ON production(user_id,status)")
  c.execute("CREATE INDEX IF NOT EXISTS idx_production_created ON production(created_at)")
  c.execute("CREATE INDEX IF NOT EXISTS idx_pauses_created ON pauses(created_at)")
def migrate_sqlite():
 if not DB.exists():return
 try:
  old=sqlite3.connect(DB);old.row_factory=sqlite3.Row
  p=old.execute("SELECT COUNT(*) n FROM production").fetchone()["n"];q=old.execute("SELECT COUNT(*) n FROM pauses").fetchone()["n"]
  if not(p or q):old.close();return
  with db() as c:
   if c.execute("SELECT COUNT(*) n FROM production").fetchone()["n"]==0:
    for r in old.execute("SELECT * FROM production"):c.execute("INSERT INTO production VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",tuple(r))
   if c.execute("SELECT COUNT(*) n FROM pauses").fetchone()["n"]==0:
    for r in old.execute("SELECT * FROM pauses"):c.execute("INSERT INTO pauses VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",tuple(r))
  old.close();logging.info("Legacy SQLite data migrated: production=%s pauses=%s",p,q)
 except Exception:logging.exception("SQLite migration skipped")
def now():return datetime.now(TZ).strftime("%H:%M")
def fmt(n):return f"{int(n):,}".replace(","," ")
def verify_init_data(init_data):
 if not init_data or not TOKEN:return None
 try:
  p=dict(parse_qsl(init_data,keep_blank_values=True));received=p.pop("hash","");auth_date=int(p.get("auth_date","0"))
  if not received or not auth_date:return None
  secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest();check=hmac.new(secret,"\n".join(f"{k}={p[k]}" for k in sorted(p)).encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(check,received):return None
  u=json.loads(p.get("user","{}"));uid=int(u.get("id",0));return {"id":uid,"name":u.get("first_name","")+((" "+u.get("last_name")) if u.get("last_name") else "")}
 except Exception:return None
def user_from_request(request):
 user=verify_init_data(request.query.get("initData","") or request.headers.get("X-Telegram-Init-Data",""))
 if user:return user
 uid=request.headers.get("X-Telegram-User-Id","").strip();name=request.headers.get("X-Telegram-User-Name","").strip()
 if uid.isdigit() and int(uid)>0:return {"id":int(uid),"name":name or "Сотрудник"}
 return None
def dash_auth(request):
 if not DASHBOARD_API_KEY:return False
 supplied=request.headers.get("X-API-Key","").strip() or request.query.get("api_key","").strip()
 return bool(supplied) and hmac.compare_digest(supplied,DASHBOARD_API_KEY)
def dash_headers():return {"Access-Control-Allow-Origin":"*","Access-Control-Allow-Headers":"X-API-Key,Content-Type","Access-Control-Allow-Methods":"GET,OPTIONS"}
def dash_unauth():return web.json_response({"error":"unauthorized"},status=401,headers=dash_headers())
def dash_where(request):
 df=request.query.get("date_from","").strip();dt=request.query.get("date_to","").strip();clauses=[];args=[]
 if df:clauses.append("created_at >= %s");args.append(df)
 if dt:clauses.append("created_at < %s");args.append(dt+"T23:59:59")
 return ((" WHERE "+" AND ".join(clauses)) if clauses else ""),args
def json_safe(rows):
 out=[]
 for r in rows:
  d=dict(r)
  for k,v in d.items():
   if isinstance(v,datetime):d[k]=v.isoformat()
  out.append(d)
 return out
async def dashboard_all(request):
 if not dash_auth(request):return dash_unauth()
 where,args=dash_where(request)
 with db() as c:
  p=c.execute("SELECT id,user_id,user_name,equipment,forming,product,start_time,end_time,quantity,status,created_at FROM production"+where+" ORDER BY created_at DESC",args).fetchall()
  q=c.execute("SELECT id,user_id,user_name,equipment,reason,start_time,end_time,created_at FROM pauses"+where+" ORDER BY created_at DESC",args).fetchall()
 return web.json_response({"ok":True,"production_count":len(p),"pause_count":len(q),"production":json_safe(p),"pauses":json_safe(q)},headers=dash_headers())
async def dashboard_production(request):
 if not dash_auth(request):return dash_unauth()
 where,args=dash_where(request)
 with db() as c:r=c.execute("SELECT id,user_id,user_name,equipment,forming,product,start_time,end_time,quantity,status,created_at FROM production"+where+" ORDER BY created_at DESC",args).fetchall()
 return web.json_response({"ok":True,"count":len(r),"items":json_safe(r)},headers=dash_headers())
async def dashboard_pauses(request):
 if not dash_auth(request):return dash_unauth()
 where,args=dash_where(request)
 with db() as c:r=c.execute("SELECT id,user_id,user_name,equipment,reason,start_time,end_time,created_at FROM pauses"+where+" ORDER BY created_at DESC",args).fetchall()
 return web.json_response({"ok":True,"count":len(r),"items":json_safe(r)},headers=dash_headers())
def panel_keyboard(bot_username):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть ФК",url=f"https://t.me/{bot_username}/production")]])
async def panel(m:Message):
 if m.chat.type not in ("group","supergroup"):return
 me=await m.bot.get_me();await m.answer("📌 **ФК — производство**\n\nВсе операции выполняются в приложении. В чат попадает только итог.",reply_markup=panel_keyboard(me.username),parse_mode="Markdown")
async def start(m:Message):
 if m.chat.type in ("group","supergroup"):await panel(m)
 elif m.chat.type=="private":
  me=await m.bot.get_me();await m.answer("📊 Откройте ФК через кнопку рабочего чата.",reply_markup=panel_keyboard(me.username))
async def web_data(m:Message):await m.answer("📱 Операции теперь выполняются в Mini App. Откройте ФК из рабочего чата.")
async def open_api(request):
 user=user_from_request(request)
 if not user:return web.json_response({"error":"unauthorized"},status=401)
 with db() as c:r=c.execute("SELECT id,equipment,forming,product,start_time FROM production WHERE user_id=%s AND status='open' ORDER BY created_at DESC",(user["id"],)).fetchall()
 return web.json_response({"items":json_safe(r)})
async def save_api(request):
 user=user_from_request(request)
 if not user:return web.json_response({"error":"unauthorized"},status=401)
 try:
  d=await request.json();event=d.get("event")
  with db() as c:
   if event=="start":
    equipment=d.get("equipment","");product=d.get("product","")
    if equipment not in PRODUCTS or product not in PRODUCTS[equipment]:return web.json_response({"error":"invalid_reference"},status=400)
    pid=str(uuid.uuid4());st=d.get("start") or now();created=datetime.now(TZ)
    c.execute("INSERT INTO production VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(pid,user["id"],user["name"],equipment,None,product,st,None,None,"open",created));text=f"🟢 **Начало производства**\n👤 {user['name']}\n🏭 {equipment}\n📦 {product}\n🕐 Начало: {st}"
   elif event=="finish":
    pid=d.get("open_id");q=int(str(d.get("quantity","0")).replace(" ",""));row=c.execute("SELECT * FROM production WHERE id=%s AND user_id=%s AND status='open'",(pid,user["id"])).fetchone()
    if not row or q<=0:return web.json_response({"error":"open_not_found"},status=404)
    et=d.get("end") or now();c.execute("UPDATE production SET end_time=%s,quantity=%s,status='closed',forming=NULL WHERE id=%s",(et,q,pid));text=f"🏁 **Завершение производства**\n👤 {user['name']}\n🏭 {row['equipment']}\n📦 {row['product']}\n🕐 Производство: {row['start_time']}–{et}\n🔢 Количество: {fmt(q)} шт"
   elif event=="pause":
    equipment=d.get("equipment","");reason=d.get("reason","")
    if equipment not in EQUIPMENT or reason not in REASONS:return web.json_response({"error":"invalid_reference"},status=400)
    pid=str(uuid.uuid4());st=d.get("start") or now();et=d.get("end") or now();c.execute("INSERT INTO pauses VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(pid,user["id"],user["name"],equipment,reason,st,et,datetime.now(TZ)));text=f"🔴 **Критическая остановка**\n👤 {user['name']}\n🏭 {equipment}\n❗ Причина: {reason}\n🕐 Период: {st}–{et}"
   else:return web.json_response({"error":"unknown_event"},status=400)
  if CHAT:await request.app["bot"].send_message(int(CHAT),text,parse_mode="Markdown")
  return web.json_response({"ok":True})
 except Exception:logging.exception("save_api failed");return web.json_response({"error":"save_failed"},status=500)
async def index(request):return web.FileResponse("/app/webapp/index.html")
async def health(request):return web.Response(text="OK",content_type="text/plain")
async def options(request):return web.Response(headers=dash_headers())
async def http_server(bot):
 app=web.Application();app["bot"]=bot;app.router.add_get("/",index);app.router.add_get("/health",health);app.router.add_get("/api/open",open_api);app.router.add_post("/api/save",save_api);app.router.add_get("/api/dashboard/production",dashboard_production);app.router.add_get("/api/dashboard/pauses",dashboard_pauses);app.router.add_get("/api/dashboard/all",dashboard_all);app.router.add_options("/api/dashboard/production",options);app.router.add_options("/api/dashboard/pauses",options);app.router.add_options("/api/dashboard/all",options);app.router.add_static("/static","/app/webapp",show_index=False);runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,"0.0.0.0",PORT);await site.start();logging.info("Mini App server listening on %s",PORT);return runner
async def main():
 if not TOKEN:raise RuntimeError("BOT_TOKEN is not set")
 init_db();migrate_sqlite();bot=Bot(TOKEN);dp=Dispatcher();dp.message.register(start,Command("start"));dp.message.register(panel,Command("setup_production"));dp.message.register(web_data,lambda m:m.web_app_data is not None);runner=await http_server(bot)
 try:await dp.start_polling(bot)
 finally:await runner.cleanup()
if __name__=="__main__":asyncio.run(main())
