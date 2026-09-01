import asyncio, hashlib, hmac, json, logging, os, sqlite3, uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN", "").strip()
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
CHAT = os.getenv("WORK_CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "8080"))
DB = Path(os.getenv("DB_PATH", "/app/data/fk.db")); DB.parent.mkdir(parents=True, exist_ok=True)
EQUIPMENT = ["cromaster", "starline", "Glimek", "König / König хлеб", "Rondo", "Trima"]
PRODUCTS = {"Холодная формовка":["Булочка бриошь зерновая","Булочка ржаная","Булочка с корицей","Булочка Сладкое сердце","Венгерская ватрушка","Круассан для сэндвича","Круассан классика мини 55 г","Круассан мини 50 г","Круассан французский 70 г","Круассан французский без дефроста","Круассан французский с сыром","Лепёшка сдобная","Лепёшка сдобная с сосиской","Начинка булочка с корицей","Начинка для пирожков с курицей и сыром","Начинка маковая для улитки","Основа для слойки с вишней","Пирожок с курицей и сыром","Рогалик вишнёвый","Слойка голландская","Слойка с вишней и заварным кремом","Слойка с марсельской сосиской","Слойка шоколад-апельсин","Творожные ушки","Трубочка","Улитка с изюмом","Улитка с маком","Хачапури","Хлеб Бородинский"],"Тёплая формовка":["Багет злаковый","Багет молочный","Багет ремесленный на опаре","Багет сырный","Батон классический","Бейгл с кунжутом","Булка Много мака","Булочка для гамбургера без кунжута","Булочка для супа тёмная","Булочка для френч-дога","Булочка для хот-дога белая","Булочка с маком","Булочка суповая светлая","Мини-чиабатта","Краюшки","Пирожок с капустой фреш","Пирожок с мясом фреш","Ромовая баба","Сочник с творогом","Хлеб бездрожжевой с семечками","Хлеб злаковый","Хлеб картофельный","Хлеб кефирный","Хлеб протеиновый","Хлеб пшеничный домашний","Хлеб с семенами чиа и пажитником","Хлеб тартин ржано-пшеничный","Хлеб тартин розовый","Хлеб тостовый молочный","Хлеб тостовый слоёный фреш","Хлеб тыквенный","Хлеб чесночный","Чиабатта пшеничная смесевая"]}
REASONS=["Поломка оборудования","Нет сырья","Нет персонала","Техническая проблема","Качество продукции"]

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
 c=db(); c.execute("CREATE TABLE IF NOT EXISTS production (id TEXT PRIMARY KEY,user_id INTEGER,user_name TEXT,equipment TEXT,forming TEXT,product TEXT,start_time TEXT,end_time TEXT,quantity INTEGER,status TEXT,created_at TEXT)"); c.execute("CREATE TABLE IF NOT EXISTS pauses (id TEXT PRIMARY KEY,user_id INTEGER,user_name TEXT,equipment TEXT,reason TEXT,start_time TEXT,end_time TEXT,created_at TEXT)"); c.commit(); c.close()

def now(): return datetime.now(TZ).strftime("%H:%M")
def fmt(n): return f"{int(n):,}".replace(","," ")

def verify_init_data(init_data):
 if not init_data or not TOKEN:return None
 try:
  p=dict(parse_qsl(init_data,keep_blank_values=True)); received=p.pop("hash",""); auth_date=int(p.get("auth_date","0"))
  if not received or not auth_date:return None
  secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest(); check=hmac.new(secret,"\n".join(f"{k}={p[k]}" for k in sorted(p)).encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(check,received):return None
  u=json.loads(p.get("user","{}")); uid=int(u.get("id",0)); return {"id":uid,"name":u.get("first_name","")+((" "+u.get("last_name")) if u.get("last_name") else "")}
 except Exception:return None

def panel_keyboard(bot_username):
 return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Открыть ФК",url=f"https://t.me/{bot_username}/production")]])

def production_text(d,finish=False):
 if finish:return f"🏁 **Завершение производства**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Производство: {d['start_time']}–{d['end_time']}\n🔢 Количество: {fmt(d['quantity'])} шт"
 return f"🟢 **Начало производства**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Начало: {d['start_time']}"

def pause_text(d):return f"🔴 **Критическая остановка**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n❗ Причина: {d['reason']}\n🕐 Период: {d['start_time']}–{d['end_time']}"

async def panel(m:Message):
 if m.chat.type not in ("group","supergroup"):return
 me=await m.bot.get_me(); await m.answer("📌 **ФК — производство**\n\nВсе операции выполняются в приложении. В чат попадает только итог.",reply_markup=panel_keyboard(me.username),parse_mode="Markdown")
async def start(m:Message):
 if m.chat.type in ("group","supergroup"):await panel(m)
 elif m.chat.type=="private":
  me=await m.bot.get_me(); await m.answer("📊 Откройте ФК через кнопку рабочего чата.",reply_markup=panel_keyboard(me.username))
async def web_data(m:Message):await m.answer("📱 Операции теперь выполняются в Mini App. Откройте ФК из рабочего чата.")

async def open_api(request):
 user=verify_init_data(request.query.get("initData",""))
 if not user or not user["id"]:return web.json_response({"error":"unauthorized"},status=401)
 c=db(); rows=c.execute("SELECT id,equipment,forming,product,start_time FROM production WHERE user_id=? AND status='open' ORDER BY created_at DESC",(user["id"],)).fetchall(); c.close(); return web.json_response({"items":[dict(r) for r in rows]})
async def save_api(request):
 user=verify_init_data(request.headers.get("X-Telegram-Init-Data",""))
 if not user or not user["id"]:return web.json_response({"error":"unauthorized"},status=401)
 c=None
 try:
  d=await request.json(); event=d.get("event"); c=db()
  if event=="start":
   pid=str(uuid.uuid4()); st=d.get("start") or now(); c.execute("INSERT INTO production VALUES(?,?,?,?,?,?,?,?,?,?,?)",(pid,user["id"],user["name"],d.get("equipment",""),d.get("group",""),d.get("product",""),st,None,None,"open",datetime.now(TZ).isoformat())); c.commit(); text=production_text({**d,"user_name":user["name"],"start_time":st})
  elif event=="finish":
   pid=d.get("open_id"); q=int(str(d.get("quantity","0")).replace(" ","")); row=c.execute("SELECT * FROM production WHERE id=? AND user_id=? AND status='open'",(pid,user["id"])).fetchone()
   if not row or q<=0:return web.json_response({"error":"open_not_found"},status=404)
   et=d.get("end") or now(); c.execute("UPDATE production SET end_time=?,quantity=?,status='closed' WHERE id=?",(et,q,pid)); c.commit(); out=dict(row); out.update(user_name=user["name"],end_time=et,quantity=q); text=production_text(out,True)
  elif event=="pause":
   pid=str(uuid.uuid4()); st=d.get("start") or now(); et=d.get("end") or now(); c.execute("INSERT INTO pauses VALUES(?,?,?,?,?,?,?,?)",(pid,user["id"],user["name"],d.get("equipment",""),d.get("reason",""),st,et,datetime.now(TZ).isoformat())); c.commit(); text=pause_text({"user_name":user["name"],"equipment":d.get("equipment",""),"reason":d.get("reason",""),"start_time":st,"end_time":et})
  else:return web.json_response({"error":"unknown_event"},status=400)
  c.close(); c=None
  if CHAT:await request.app["bot"].send_message(int(CHAT),text,parse_mode="Markdown")
  return web.json_response({"ok":True})
 except Exception:
  if c:c.close()
  logging.exception("save_api failed"); return web.json_response({"error":"save_failed"},status=500)

async def index(request):return web.FileResponse("/app/webapp/index.html")
async def health(request):return web.Response(text="OK",content_type="text/plain")
async def http_server(bot):
 app=web.Application(); app["bot"]=bot; app.router.add_get("/",index); app.router.add_get("/health",health); app.router.add_get("/api/open",open_api); app.router.add_post("/api/save",save_api); app.router.add_static("/static","/app/webapp",show_index=False)
 runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,"0.0.0.0",PORT); await site.start(); logging.info("Mini App server listening on %s",PORT); return runner
async def main():
 if not TOKEN:raise RuntimeError("BOT_TOKEN is not set")
 init_db(); bot=Bot(TOKEN); dp=Dispatcher(); dp.message.register(start,Command("start")); dp.message.register(panel,Command("setup_production")); dp.message.register(web_data,lambda m:m.web_app_data is not None); runner=await http_server(bot)
 try:await dp.start_polling(bot)
 finally:await runner.cleanup()
if __name__=="__main__":asyncio.run(main())
