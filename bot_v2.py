import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
WORK_CHAT_ID_RAW = os.getenv("WORK_CHAT_ID", "").strip()
WORK_CHAT_ID = int(WORK_CHAT_ID_RAW) if WORK_CHAT_ID_RAW else None

EQUIPMENT = ["cromaster", "starline", "Glimek", "König / König хлеб", "Rondo", "Trima"]
PRODUCT_GROUPS = {
    "Холодная формовка": ["Булочка бриошь зерновая","Булочка ржаная","Булочка с корицей","Булочка Сладкое сердце","Венгерская ватрушка","Круассан для сэндвича","Круассан классика мини 55 г","Круассан мини 50 г","Круассан французский 70 г","Круассан французский без дефроста","Круассан французский с сыром","Лепёшка сдобная","Лепёшка сдобная с сосиской","Начинка булочка с корицей","Начинка для пирожков с курицей и сыром","Начинка маковая для улитки","Основа для слойки с вишней","Пирожок с курицей и сыром","Рогалик вишнёвый","Слойка голландская","Слойка с вишней и заварным кремом","Слойка с марсельской сосиской","Слойка шоколад-апельсин","Творожные ушки","Трубочка","Улитка с изюмом","Улитка с маком","Хачапури","Хлеб Бородинский"],
    "Тёплая формовка": ["Багет злаковый","Багет молочный","Багет ремесленный на опаре","Багет сырный","Батон классический","Бейгл с кунжутом","Булка Много мака","Булочка для гамбургера без кунжута","Булочка для супа тёмная","Булочка для френч-дога","Булочка для хот-дога белая","Булочка с маком","Булочка суповая светлая","Мини-чиабатта","Краюшки","Пирожок с капустой фреш","Пирожок с мясом фреш","Ромовая баба","Сочник с творогом","Хлеб бездрожжевой с семечками","Хлеб злаковый","Хлеб картофельный","Хлеб кефирный","Хлеб протеиновый","Хлеб пшеничный домашний","Хлеб с семенами чиа и пажитником","Хлеб тартин ржано-пшеничный","Хлеб тартин розовый","Хлеб тостовый молочный","Хлеб тостовый слоёный фреш","Хлеб тыквенный","Хлеб чесночный","Чиабатта пшеничная смесевая"]
}
REASONS = ["Поломка оборудования","Нет сырья","Нет персонала","Техническая проблема","Качество продукции"]

class Form(StatesGroup):
    event = State(); equipment = State(); product_group = State(); product = State(); start_time = State(); end_time = State(); quantity = State(); reason = State(); preview = State(); text = State()

def kb(rows, cols=2):
    b = InlineKeyboardBuilder()
    for text, data in rows: b.button(text=text, callback_data=data)
    b.adjust(cols)
    return b.as_markup()

def group_button(bot_username):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 Внести данные о производстве", url=f"https://t.me/{bot_username}?start=production")]])

def main_kb(): return kb([("🟢 Начало производства","event:start"),("🏁 Завершение производства","event:stop"),("🔴 Остановка — критическая","event:pause")],1)
def equipment_kb(): return kb([(x,f"equipment:{i}") for i,x in enumerate(EQUIPMENT)]+[("✏️ Другое","equipment:other")],2)
def group_kb(): return kb([(x,f"group:{i}") for i,x in enumerate(PRODUCT_GROUPS)],1)
def time_kb(prefix="time"): return kb([("🕐 Сейчас",f"{prefix}:now"),("⌨️ Ввести время",f"{prefix}:manual")],1)
def reason_kb(): return kb([(x,f"reason:{i}") for i,x in enumerate(REASONS)]+[("✏️ Другая причина","reason:other")],1)
def confirm_kb(): return kb([("✅ Всё верно — опубликовать","confirm:yes"),("✏️ Исправить","confirm:edit"),("❌ Отмена","confirm:no")],1)
def valid_time(s):
    try: datetime.strptime(s,"%H:%M"); return True
    except ValueError: return False

def search_products(group, query):
    q=query.lower().strip()
    return [p for p in PRODUCT_GROUPS[group] if q in p.lower()]

def build_text(d):
    if d["event"]=="start": return f"🟢 **Начало производства**\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Старт: {d['start_time']}"
    if d["event"]=="stop": return f"🏁 **Завершение производства**\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 Стоп: {d['start_time']}\n🔢 Количество: {d['quantity']} шт"
    return f"🔴 **Остановка — критическая**\n🏭 {d['equipment']}\n📦 {d['product']}\n🕐 С {d['start_time']} до {d['end_time']}\n❗ Причина: {d['reason']}"

async def show_menu(message,state):
    await state.clear(); await message.answer("📊 **Производство**\n\nЧто нужно записать?",reply_markup=main_kb(),parse_mode="Markdown")

async def start(message,state):
    if message.chat.type!="private": return
    await show_menu(message,state)

async def setup(message,bot):
    if message.chat.type not in ("group","supergroup"): return await message.answer("Команду /setup_production нужно выполнить в рабочем групповом чате.")
    me=await bot.get_me()
    await message.answer("📊 **Внесение данных о производстве**\n\nНажмите кнопку. Заполнение откроется в вашем личном диалоге с ботом. Другие сотрудники его не увидят. В общий чат попадёт только итог.",reply_markup=group_button(me.username),parse_mode="Markdown")

async def event(call,state):
    await state.clear(); await state.update_data(event=call.data.split(":")[1]); await state.set_state(Form.equipment); await call.message.edit_text("🏭 **Выберите оборудование:**",reply_markup=equipment_kb(),parse_mode="Markdown"); await call.answer()

async def equipment(call,state):
    v=call.data.split(":")[1]
    if v=="other": await state.update_data(waiting="equipment"); await state.set_state(Form.text); await call.message.edit_text("🏭 **Напишите название оборудования:**",parse_mode="Markdown")
    else: await state.update_data(equipment=EQUIPMENT[int(v)]); await state.set_state(Form.product_group); await call.message.edit_text("📦 **Какой вид формовки?**",reply_markup=group_kb(),parse_mode="Markdown")
    await call.answer()

async def group(call,state):
    i=int(call.data.split(":")[1]); name=list(PRODUCT_GROUPS)[i]; await state.update_data(product_group=name); await state.set_state(Form.product); await call.message.edit_text("🔎 **Найдём продукцию**\n\nНапишите несколько букв или слово. Например: `хлеб`, `круассан`, `пирожок`.",parse_mode="Markdown"); await call.answer()

async def time_choice(call,state):
    mode=call.data.split(":")[1]
    if mode=="manual": await state.set_state(Form.start_time); await call.message.edit_text("⌨️ **Напишите время**, например `08:30`.",parse_mode="Markdown"); await call.answer(); return
    await set_start_time(datetime.now(TIMEZONE).strftime("%H:%M"),call.message,state); await call.answer()

async def end_time_choice(call,state):
    mode=call.data.split(":")[1]
    if mode=="manual": await state.set_state(Form.end_time); await call.message.edit_text("⌨️ **Напишите время окончания**, например `09:45`.",parse_mode="Markdown"); await call.answer(); return
    await state.update_data(end_time=datetime.now(TIMEZONE).strftime("%H:%M")); await state.set_state(Form.reason); await call.message.edit_text("❓ **Почему произошла критическая остановка?**",reply_markup=reason_kb(),parse_mode="Markdown"); await call.answer()

async def set_start_time(value,message,state):
    await state.update_data(start_time=value); d=await state.get_data()
    if d["event"]=="pause": await state.set_state(Form.end_time); await message.edit_text("🕐 **Когда закончилась критическая остановка?**",reply_markup=time_kb("endtime"),parse_mode="Markdown")
    elif d["event"]=="stop": await state.set_state(Form.quantity); await message.edit_text("🔢 **Сколько штук произвели?**\n\nМожно написать `8280`.",parse_mode="Markdown")
    else: await preview(message,state)

async def reason(call,state):
    v=call.data.split(":")[1]
    if v=="other": await state.update_data(waiting="reason"); await state.set_state(Form.text); await call.message.edit_text("❓ **Напишите причину своими словами:**",parse_mode="Markdown")
    else: await state.update_data(reason=REASONS[int(v)]); await preview(call.message,state)
    await call.answer()

async def preview(message,state):
    d=await state.get_data(); await state.set_state(Form.preview); await message.edit_text("**Проверьте, всё ли правильно:**\n\n"+build_text(d),reply_markup=confirm_kb(),parse_mode="Markdown")

async def text_input(message,state):
    if message.chat.type!="private": return
    d=await state.get_data(); current=await state.get_state(); text=(message.text or "").strip(); waiting=d.get("waiting")
    if waiting=="equipment": await state.update_data(equipment=text,waiting=None); await state.set_state(Form.product_group); await message.answer("📦 **Какой вид формовки?**",reply_markup=group_kb(),parse_mode="Markdown"); return
    if waiting=="reason": await state.update_data(reason=text,waiting=None); await preview(message,state); return
    if current==Form.product.state:
        if text.lower() in {"другое","нет","нет в списке"}: await state.update_data(product="Другое / нет в справочнике"); await state.set_state(Form.start_time); await message.answer("🕐 **Когда начали?**",reply_markup=time_kb(),parse_mode="Markdown"); return
        results=search_products(d["product_group"],text)
        if not results: await message.answer("😕 Не нашёл. Попробуйте проще: `хлеб`, `круассан`, `багет`, `пирожок`.\n\nЕсли продукции нет в списке — напишите **другое**.",parse_mode="Markdown"); return
        await state.update_data(search_results=results); rows=[(f"📦 {p}",f"product:{i}") for i,p in enumerate(results[:20])]+[("✏️ Другое / нет в списке","product:other")]; await message.answer(f"🔎 **Нашёл вариантов: {len(results)}**\n\nВыберите нужный:",reply_markup=kb(rows,1),parse_mode="Markdown"); return
    if current==Form.start_time.state:
        if not valid_time(text): return await message.answer("⚠️ Не понял время. Напишите, например `08:30`.",parse_mode="Markdown")
        return await set_start_time(text,message,state)
    if current==Form.end_time.state:
        if not valid_time(text): return await message.answer("⚠️ Не понял время. Напишите, например `09:45`.",parse_mode="Markdown")
        await state.update_data(end_time=text); await state.set_state(Form.reason); return await message.answer("❓ **Почему произошла критическая остановка?**",reply_markup=reason_kb(),parse_mode="Markdown")
    if current==Form.quantity.state:
        cleaned=text.lower().replace(" ","").replace("шт","")
        if not cleaned.isdigit(): return await message.answer("⚠️ Нужна только цифра. Например `8280`.",parse_mode="Markdown")
        await state.update_data(quantity=int(cleaned)); return await preview(message,state)
    await message.answer("Нажмите кнопку «📊 Внести данные о производстве» в рабочем чате.")

async def product(call,state):
    v=call.data.split(":")[1]; d=await state.get_data()
    if v=="other": await state.update_data(product="Другое / нет в справочнике")
    else:
        results=d.get("search_results",[]); i=int(v)
        if i>=len(results): return await call.answer("Вариант недоступен",show_alert=True)
        await state.update_data(product=results[i])
    await state.set_state(Form.start_time); await call.message.edit_text("🕐 **Когда начали?**",reply_markup=time_kb(),parse_mode="Markdown"); await call.answer()

async def confirm(call,state,bot):
    action=call.data.split(":")[1]
    if action=="no": await state.clear(); await call.message.edit_text("❌ Запись отменена."); return await call.answer()
    if action=="edit": await state.set_state(Form.equipment); await call.message.edit_text("🏭 **Выберите оборудование заново:**",reply_markup=equipment_kb(),parse_mode="Markdown"); return await call.answer()
    d=await state.get_data()
    if not WORK_CHAT_ID: return await call.answer("Не задан WORK_CHAT_ID",show_alert=True)
    await bot.send_message(WORK_CHAT_ID,build_text(d),parse_mode="Markdown"); await state.clear(); await call.message.edit_text("✅ **Готово!**\n\nИтог опубликован в рабочем чате.",parse_mode="Markdown"); await call.answer()

async def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    bot=Bot(BOT_TOKEN); dp=Dispatcher()
    dp.message.register(setup,Command("setup_production")); dp.message.register(start,Command("start")); dp.message.register(start,Command("menu"))
    dp.callback_query.register(event,F.data.startswith("event:")); dp.callback_query.register(equipment,F.data.startswith("equipment:")); dp.callback_query.register(group,F.data.startswith("group:")); dp.callback_query.register(product,F.data.startswith("product:")); dp.callback_query.register(time_choice,F.data.startswith("time:")); dp.callback_query.register(end_time_choice,F.data.startswith("endtime:")); dp.callback_query.register(reason,F.data.startswith("reason:")); dp.callback_query.register(confirm,F.data.startswith("confirm:")); dp.message.register(text_input)
    me=await bot.get_me(); logging.info("Production bot started as @%s; work_chat_id=%s; timezone=%s",me.username,WORK_CHAT_ID,TIMEZONE); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
