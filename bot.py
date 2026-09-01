import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
BOT_TOKEN=os.getenv('BOT_TOKEN')
TIMEZONE=ZoneInfo(os.getenv('TIMEZONE','Europe/Moscow'))
WORK_CHAT_ID_RAW=os.getenv('WORK_CHAT_ID','').strip()
WORK_CHAT_ID=int(WORK_CHAT_ID_RAW) if WORK_CHAT_ID_RAW else None
EQUIPMENT=['cromaster','starline','Glimek','König / König хлеб','Rondo','Trima']
PRODUCT_GROUPS={'Холодная формовка':['Булочка бриошь зерновая','Булочка ржаная','Булочка с корицей','Булочка Сладкое сердце','Венгерская ватрушка','Круассан для сэндвича','Круассан классика мини 55 г','Круассан мини 50 г','Круассан французский 70 г','Круассан французский без дефроста','Круассан французский с сыром','Лепёшка сдобная','Лепёшка сдобная с сосиской','Начинка булочка с корицей','Начинка для пирожков с курицей и сыром','Начинка маковая для улитки','Основа для слойки с вишней','Пирожок с курицей и сыром','Рогалик вишнёвый','Слойка голландская','Слойка с вишней и заварным кремом','Слойка с марсельской сосиской','Слойка шоколад-апельсин','Творожные ушки','Трубочка','Улитка с изюмом','Улитка с маком','Хачапури','Хлеб Бородинский'],'Тёплая формовка':['Багет злаковый','Багет молочный','Багет ремесленный на опаре','Багет сырный','Батон классический','Бейгл с кунжутом','Булка Много мака','Булочка для гамбургера без кунжута','Булочка для супа тёмная','Булочка для френч-дога','Булочка для хот-дога белая','Булочка с маком','Булочка суповая светлая','Мини-чиабатта','Краюшки','Пирожок с капустой фреш','Пирожок с мясом фреш','Ромовая баба','Сочник с творогом','Хлеб бездрожжевой с семечками','Хлеб злаковый','Хлеб картофельный','Хлеб кефирный','Хлеб протеиновый','Хлеб пшеничный домашний','Хлеб с семенами чиа и пажитником','Хлеб тартин ржано-пшеничный','Хлеб тартин розовый','Хлеб тостовый молочный','Хлеб тостовый слоёный фреш','Хлеб тыквенный','Хлеб чесночный','Чиабатта пшеничная смесевая']}
REASONS=['Поломка оборудования','Нет сырья','Нет персонала','Техническая проблема','Качество продукции']
class Form(StatesGroup): event=State(); equipment=State(); group=State(); product=State(); start=State(); end=State(); quantity=State(); reason=State(); other=State(); confirm=State()
def kb(rows,cols=1):
 b=InlineKeyboardBuilder()
 for t,d in rows:b.button(text=t,callback_data=d)
 b.adjust(cols);return b.as_markup()
def now():return datetime.now(TIMEZONE).strftime('%H:%M')
def valid(v):
 try:datetime.strptime(v,'%H:%M');return True
 except:return False
def result(d):
 if d['event']=='start':return f"🟢 **Начало производства**\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Старт: {d['start']}"
 if d['event']=='finish':return f"🏁 **Завершение производства**\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Завершение: {d['start']}\n🔢 Количество: {d['quantity']} шт"
 return f"🔴 **Критическая остановка**\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 С {d['start']} до {d['end']}\n❗ Причина: {d['reason']}"
async def menu(m: Message, s: FSMContext):
 await s.clear();await m.answer('📊 **Производство**\n\nЧто записываем?',reply_markup=kb([('🟢 Начало производства','e:start'),('🏁 Завершение производства','e:finish'),('🔴 Остановка — критическая','e:pause')]),parse_mode='Markdown')
async def setup(m: Message, bot: Bot):
 if m.chat.type not in ('group','supergroup'):return
 me=await bot.get_me();await m.answer('📊 **Внесение данных о производстве**\n\nЗаполнение откроется в личном диалоге с ботом. В общий чат попадёт только итог.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📊 Внести данные о производстве',url=f'https://t.me/{me.username}?start=production')]]),parse_mode='Markdown')
async def callbacks(c: CallbackQuery, s: FSMContext):
 x=c.data;d=await s.get_data()
 if x.startswith('e:'):await s.clear();await s.update_data(event=x[2:]);await s.set_state(Form.equipment);await c.message.edit_text('🏭 **Выберите оборудование:**',reply_markup=kb([(v,f'q:{i}') for i,v in enumerate(EQUIPMENT)]+[('✏️ Другое','q:other')],2),parse_mode='Markdown')
 elif x.startswith('q:'):
  v=x[2:]
  if v=='other':await s.update_data(wait='equipment');await s.set_state(Form.other);await c.message.edit_text('🏭 Напишите название оборудования:')
  else:await s.update_data(equipment=EQUIPMENT[int(v)]);await s.set_state(Form.group);await c.message.edit_text('📦 **Выберите вид формовки:**',reply_markup=kb([(v,f'g:{i}') for i,v in enumerate(PRODUCT_GROUPS)]),parse_mode='Markdown')
 elif x.startswith('g:'):
  g=list(PRODUCT_GROUPS)[int(x[2:])];await s.update_data(group=g);await s.set_state(Form.product);await c.message.edit_text('🔎 **Напишите несколько букв продукции.**\nНапример: `хлеб`, `бейгл`, `пирожок`.',parse_mode='Markdown')
 elif x.startswith('p:'):
  r=d.get('results',[]);v=x[2:];await s.update_data(product='Другое / нет в справочнике' if v=='other' else r[int(v)]);await s.set_state(Form.start);await c.message.edit_text('🕐 **Когда начали?**',reply_markup=kb([('🕐 Сейчас','t:now'),('⌨️ Ввести время','t:manual')]),parse_mode='Markdown')
 elif x.startswith('t:'):
  v=x[2:]
  if v=='manual':await s.set_state(Form.start);await c.message.edit_text('⌨️ Напишите время, например `08:30`.',parse_mode='Markdown')
  else:await s.update_data(start=now());await after_start(c.message,s)
 elif x.startswith('et:'):
  v=x[3:]
  if v=='manual':await s.set_state(Form.end);await c.message.edit_text('⌨️ Напишите время окончания, например `09:45`.',parse_mode='Markdown')
  else:await s.update_data(end=now());await s.set_state(Form.reason);await c.message.edit_text('❓ **Почему произошла критическая остановка?**',reply_markup=kb([(v,f'r:{i}') for i,v in enumerate(REASONS)]+[('✏️ Другая причина','r:other')]),parse_mode='Markdown')
 elif x.startswith('r:'):
  v=x[2:]
  if v=='other':await s.update_data(wait='reason');await s.set_state(Form.other);await c.message.edit_text('❓ Напишите причину своими словами:')
  else:await s.update_data(reason=REASONS[int(v)]);await preview(c.message,s)
 elif x.startswith('c:'):
  if x=='c:no':await s.clear();await c.message.edit_text('❌ Запись отменена.');return await c.answer()
  if x=='c:edit':await s.set_state(Form.equipment);await c.message.edit_text('🏭 **Выберите оборудование заново:**',reply_markup=kb([(v,f'q:{i}') for i,v in enumerate(EQUIPMENT)],2),parse_mode='Markdown');return await c.answer()
  d=await s.get_data()
  if not WORK_CHAT_ID:return await c.answer('WORK_CHAT_ID не задан',show_alert=True)
  await c.bot.send_message(WORK_CHAT_ID,result(d),parse_mode='Markdown');await s.clear();await c.message.edit_text('✅ **Готово! Итог опубликован.**',parse_mode='Markdown')
 await c.answer()
async def after_start(m: Message,s: FSMContext):
 d=await s.get_data()
 if d['event']=='finish':await s.set_state(Form.quantity);await m.edit_text('🔢 **Сколько штук произвели?**\nНапример: `8280`',parse_mode='Markdown')
 elif d['event']=='pause':await s.set_state(Form.end);await m.edit_text('🕐 **Когда закончилась остановка?**',reply_markup=kb([('🕐 Сейчас','et:now'),('⌨️ Ввести время','et:manual')]),parse_mode='Markdown')
 else:await preview(m,s)
async def preview(m: Message,s: FSMContext):await s.set_state(Form.confirm);await m.edit_text('**Проверьте запись:**\n\n'+result(await s.get_data()),reply_markup=kb([('✅ Всё верно — опубликовать','c:yes'),('✏️ Исправить','c:edit'),('❌ Отмена','c:no')]),parse_mode='Markdown')
async def input_msg(m: Message,s: FSMContext):
 if m.chat.type!='private':return
 d=await s.get_data();cur=await s.get_state();v=(m.text or '').strip();w=d.get('wait')
 if w=='equipment':await s.update_data(equipment=v,wait=None);await s.set_state(Form.group);await m.answer('📦 **Выберите вид формовки:**',reply_markup=kb([(x,f'g:{i}') for i,x in enumerate(PRODUCT_GROUPS)]),parse_mode='Markdown');return
 if w=='reason':await s.update_data(reason=v,wait=None);await preview(m,s);return
 if cur==Form.product.state:
  if v.lower() in ('другое','нет','нет в списке'):await s.update_data(product='Другое / нет в справочнике');await s.set_state(Form.start);await m.answer('🕐 **Когда начали?**',reply_markup=kb([('🕐 Сейчас','t:now'),('⌨️ Ввести время','t:manual')]),parse_mode='Markdown');return
  r=[p for p in PRODUCT_GROUPS[d['group']] if v.lower() in p.lower()]
  if not r:return await m.answer('😕 Не нашёл. Попробуйте проще или напишите **другое**.',parse_mode='Markdown')
  await s.update_data(results=r);await m.answer('🔎 **Выберите продукцию:**',reply_markup=kb([(p,f'p:{i}') for i,p in enumerate(r[:20])]+[('✏️ Другое / нет в списке','p:other')]),parse_mode='Markdown')
 elif cur==Form.start.state:
  if not valid(v):return await m.answer('⚠️ Напишите время в формате `08:30`.',parse_mode='Markdown')
  await s.update_data(start=v);await after_start(m,s)
 elif cur==Form.end.state:
  if not valid(v):return await m.answer('⚠️ Напишите время в формате `09:45`.',parse_mode='Markdown')
  await s.update_data(end=v);await s.set_state(Form.reason);await m.answer('❓ **Почему произошла критическая остановка?**',reply_markup=kb([(x,f'r:{i}') for i,x in enumerate(REASONS)]+[('✏️ Другая причина','r:other')]),parse_mode='Markdown')
 elif cur==Form.quantity.state:
  q=v.replace(' ','').replace('шт','')
  if not q.isdigit():return await m.answer('⚠️ Введите количество цифрами, например `8280`.')
  await s.update_data(quantity=int(q));await preview(m,s)
async def chatid(m: Message):
 if m.chat.type in ('group','supergroup'):await m.answer(f'🆔 WORK_CHAT_ID для этого чата:\n`{m.chat.id}`',parse_mode='Markdown')
async def main():
 if not BOT_TOKEN:raise RuntimeError('BOT_TOKEN is not set')
 bot=Bot(BOT_TOKEN);dp=Dispatcher();dp.message.register(setup,Command('setup_production'));dp.message.register(chatid,Command('chatid'));dp.message.register(menu,Command('start'));dp.message.register(menu,Command('menu'));dp.callback_query.register(callbacks);dp.message.register(input_msg);logging.info('Production bot started; work_chat_id=%s; timezone=%s',WORK_CHAT_ID,TIMEZONE);await dp.start_polling(bot)
if __name__=='__main__':asyncio.run(main())
