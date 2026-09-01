import asyncio, json, logging, os, sqlite3, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv('BOT_TOKEN'); TZ=ZoneInfo(os.getenv('TIMEZONE','Europe/Moscow')); CHAT=os.getenv('WORK_CHAT_ID','').strip(); WEBAPP_URL=os.getenv('WEBAPP_URL','').strip(); PORT=int(os.getenv('PORT','8080'))
DB=Path(os.getenv('DB_PATH','/app/data/fk.db')); DB.parent.mkdir(parents=True,exist_ok=True)

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
 c=db(); c.execute('''CREATE TABLE IF NOT EXISTS production (id TEXT PRIMARY KEY,user_id INTEGER,user_name TEXT,equipment TEXT,forming TEXT,product TEXT,start_time TEXT,end_time TEXT,quantity INTEGER,status TEXT,created_at TEXT)'''); c.execute('''CREATE TABLE IF NOT EXISTS pauses (id TEXT PRIMARY KEY,user_id INTEGER,user_name TEXT,equipment TEXT,reason TEXT,start_time TEXT,end_time TEXT,created_at TEXT)'''); c.commit(); c.close()

def now(): return datetime.now(TZ).strftime('%H:%M')

def keyboard():
 if not WEBAPP_URL: return None
 return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📱 Открыть ФК',web_app=WebAppInfo(url=WEBAPP_URL))]])

def fmt(n): return f'{int(n):,}'.replace(',',' ')

def production_text(d,finish=False):
 if finish:
  return f"🏁 **Завершение производства**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Производство: {d['start_time']}–{d['end_time']}\n🔢 Количество: {fmt(d['quantity'])} шт"
 return f"🟢 **Начало производства**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Начало: {d['start_time']}"

def pause_text(d): return f"🔴 **Критическая остановка**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n❗ Причина: {d['reason']}\n🕐 Период: {d['start_time']}–{d['end_time']}"

async def panel(m:Message):
 if m.chat.type not in ('group','supergroup'): return
 if not WEBAPP_URL:
  await m.answer('⚠️ WEBAPP_URL ещё не задан. Добавьте публичный HTTPS-адрес Mini App в переменные окружения.')
  return
 sent=await m.answer('📌 **ФК — производство**\n\nВсе операции выполняются в приложении. В чат попадает только итог.',reply_markup=keyboard(),parse_mode='Markdown')
 try: await m.bot.pin_chat_message(m.chat.id,sent.message_id,disable_notification=True)
 except Exception: logging.exception('pin failed')

async def start(m:Message):
 if m.chat.type in ('group','supergroup'): await panel(m)
 elif m.chat.type=='private': await m.answer('📊 Откройте ФК через закреплённую кнопку рабочего чата.',reply_markup=keyboard())

async def web_data(m:Message):
 try: d=json.loads(m.web_app_data.data)
 except Exception: await m.answer('⚠️ Не удалось прочитать данные формы.'); return
 user_id=m.from_user.id; user_name=m.from_user.full_name; event=d.get('event')
 try:
  c=db()
  if event=='start':
   pid=str(uuid.uuid4()); c.execute('INSERT INTO production VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pid,user_id,user_name,d.get('equipment',''),d.get('group') or d.get('forming',''),d.get('product',''),d.get('start') or now(),None,None,'open',datetime.now(TZ).isoformat())); c.commit();
   out={**d,'user_name':user_name,'start_time':d.get('start') or now()}; text=production_text(out)
  elif event=='finish':
   pid=d.get('open_id'); q=int(str(d.get('quantity','0')).replace(' ',''));
   if not pid or q<=0: raise ValueError('bad finish')
   row=c.execute('SELECT * FROM production WHERE id=? AND user_id=? AND status="open"',(pid,user_id)).fetchone()
   if not row: await m.answer('⚠️ Открытое производство не найдено.'); c.close(); return
   end=d.get('end') or now(); c.execute('UPDATE production SET end_time=?,quantity=?,status="closed" WHERE id=?',(end,q,pid)); c.commit(); out=dict(row); out.update(user_name=user_name,end_time=end,quantity=q); text=production_text(out,True)
  elif event=='pause':
   pid=str(uuid.uuid4()); start_t=d.get('start') or now(); end_t=d.get('end') or now(); c.execute('INSERT INTO pauses VALUES(?,?,?,?,?,?,?,?)',(pid,user_id,user_name,d.get('equipment',''),d.get('reason',''),start_t,end_t,datetime.now(TZ).isoformat())); c.commit(); out={'user_name':user_name,'equipment':d.get('equipment',''),'reason':d.get('reason',''),'start_time':start_t,'end_time':end_t}; text=pause_text(out)
  else: raise ValueError('unknown event')
  c.close()
  if CHAT: await m.bot.send_message(int(CHAT),text,parse_mode='Markdown')
  await m.answer('✅ Готово! Итог опубликован в рабочем чате.')
 except Exception:
  logging.exception('web_app_data failed'); await m.answer('⚠️ Не удалось сохранить операцию. Попробуйте ещё раз.')

async def open_api(request:web.Request):
 try: uid=int(request.query.get('user_id','0'))
 except: uid=0
 c=db(); rows=c.execute('SELECT id,equipment,forming,product,start_time FROM production WHERE user_id=? AND status="open" ORDER BY created_at DESC',(uid,)).fetchall(); c.close()
 return web.json_response({'items':[dict(r) for r in rows]})

async def index(request:web.Request): return web.FileResponse('/app/webapp/index.html')

async def http_server():
 app=web.Application(); app.router.add_get('/',index); app.router.add_get('/api/open',open_api); app.router.add_static('/static','/app/webapp',show_index=False)
 runner=web.AppRunner(app); await runner.setup(); site=web.TCPSite(runner,'0.0.0.0',PORT); await site.start(); logging.info('WebApp server listening on %s',PORT); return runner

async def main():
 if not TOKEN: raise RuntimeError('BOT_TOKEN is not set')
 init_db(); bot=Bot(TOKEN); dp=Dispatcher(); dp.message.register(start,Command('start')); dp.message.register(panel,Command('setup_production')); dp.message.register(web_data,lambda m:m.web_app_data is not None)
 runner=await http_server()
 try: await dp.start_polling(bot)
 finally: await runner.cleanup()

if __name__=='__main__': asyncio.run(main())
