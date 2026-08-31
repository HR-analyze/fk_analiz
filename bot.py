import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
WORK_CHAT_ID = int(os.getenv("WORK_CHAT_ID")) if os.getenv("WORK_CHAT_ID", "").strip() else None

EQUIPMENT = ["Кёниг 1", "Кёниг 2", "Рондо 1", "Рондо 2"]
PRODUCTS = ["Бейгл 120г", "Багет злаковый 250г", "Пирожки"]
REASONS = ["Подготовка форм", "Переналадка", "Поломка", "Нет сырья", "Нет персонала", "Санитарная обработка"]

class Form(StatesGroup):
    equipment = State(); product = State(); start_time = State(); end_time = State(); quantity = State(); reason = State(); preview = State()

def kb(rows, cols=2):
    b = InlineKeyboardBuilder()
    for text, data in rows: b.button(text=text, callback_data=data)
    b.adjust(cols); return b.as_markup()

def main_kb(): return kb([("🟢 Старт производства","event:start"),("🔴 Стоп производства","event:stop"),("⏸ Остановка","event:pause")],1)
def equipment_kb(): return kb([(x,f"equipment:{i}") for i,x in enumerate(EQUIPMENT)]+[("Другое","equipment:other")])
def product_kb(): return kb([(x,f"product:{i}") for i,x in enumerate(PRODUCTS)]+[("Другое","product:other")])
def time_kb(prefix="time"): return kb([("🕐 Сейчас",f"{prefix}:now"),("⌨️ Ввести время",f"{prefix}:manual")],1)
def reason_kb(): return kb([(x,f"reason:{i}") for i,x in enumerate(REASONS)]+[("Другая","reason:other")])
def confirm_kb(): return kb([("✅ Опубликовать","confirm:yes"),("❌ Отмена","confirm:no")],1)

def valid_time(s):
    try: datetime.strptime(s,"%H:%M"); return True
    except ValueError: return False

def build_text(d):
    if d["event"]=="start": return f"🟢 **Старт {d['equipment']} – {d['product']} | Старт: {d['start_time']}**"
    if d["event"]=="stop": return f"🔴 **Стоп {d['equipment']} – {d['product']} | Стоп: {d['start_time']} | Количество: {d['quantity']} шт**"
    return f"⏸ **Остановка {d['equipment']} – {d['product']} | с {d['start_time']} до {d['end_time']} | Причина: {d['reason']}**"

async def menu(message: Message, state: FSMContext):
    await state.clear(); await message.answer("📊 **Производство**\n\nВыберите действие:",reply_markup=main_kb(),parse_mode="Markdown")

async def event(call: CallbackQuery,state: FSMContext):
    await state.clear(); await state.update_data(event=call.data.split(":")[1]); await state.set_state(Form.equipment)
    await call.message.edit_text("🏭 **Выберите оборудование:**",reply_markup=equipment_kb(),parse_mode="Markdown"); await call.answer()

async def equipment(call: CallbackQuery,state: FSMContext):
    v=call.data.split(":")[1]
    if v=="other": await state.update_data(waiting="equipment"); await call.message.answer("🏭 Напишите название оборудования:")
    else:
        await state.update_data(equipment=EQUIPMENT[int(v)]); await state.set_state(Form.product); await call.message.edit_text("🥐 **Выберите продукт:**",reply_markup=product_kb(),parse_mode="Markdown")
    await call.answer()

async def product(call: CallbackQuery,state: FSMContext):
    v=call.data.split(":")[1]
    if v=="other": await state.update_data(waiting="product"); await call.message.answer("🥐 Напишите название продукта:")
    else:
        await state.update_data(product=PRODUCTS[int(v)]); await state.set_state(Form.start_time); await call.message.edit_text("🕐 **Укажите время:**",reply_markup=time_kb(),parse_mode="Markdown")
    await call.answer()

async def time_choice(call: CallbackQuery,state: FSMContext):
    v=call.data.split(":")[1]
    if v=="manual": await call.message.answer("Введите время в формате ЧЧ:ММ, например 08:30:"); await call.answer(); return
    await set_start_time(datetime.now(TIMEZONE).strftime("%H:%M"),call.message,state); await call.answer()

async def end_time_choice(call: CallbackQuery,state: FSMContext):
    v=call.data.split(":")[1]
    if v=="manual": await call.message.answer("Введите время окончания в формате ЧЧ:ММ:"); await call.answer(); return
    await state.update_data(end_time=datetime.now(TIMEZONE).strftime("%H:%M")); await state.set_state(Form.reason)
    await call.message.edit_text("❓ **Выберите причину остановки:**",reply_markup=reason_kb(),parse_mode="Markdown"); await call.answer()

async def set_start_time(value,message,state):
    await state.update_data(start_time=value); d=await state.get_data()
    if d["event"]=="pause": await state.set_state(Form.end_time); await message.edit_text("🕐 **Укажите время окончания остановки:**",reply_markup=time_kb("endtime"),parse_mode="Markdown")
    elif d["event"]=="stop": await state.set_state(Form.quantity); await message.edit_text("🔢 **Укажите количество произведено (шт):**",parse_mode="Markdown")
    else: await preview(message,state)

async def reason(call: CallbackQuery,state: FSMContext):
    v=call.data.split(":")[1]
    if v=="other": await state.update_data(waiting="reason"); await call.message.answer("❓ Напишите причину остановки:")
    else: await state.update_data(reason=REASONS[int(v)]); await preview(call.message,state)
    await call.answer()

async def preview(message,state):
    d=await state.get_data(); await state.set_state(Form.preview); await message.answer("**Проверьте запись:**\n\n"+build_text(d),reply_markup=confirm_kb(),parse_mode="Markdown")

async def text_input(message:Message,state:FSMContext):
    d=await state.get_data(); w=d.get("waiting"); text=(message.text or "").strip(); cur=await state.get_state()
    if w=="equipment": await state.update_data(equipment=text,waiting=None); await state.set_state(Form.product); await message.answer("🥐 **Выберите продукт:**",reply_markup=product_kb(),parse_mode="Markdown"); return
    if w=="product": await state.update_data(product=text,waiting=None); await state.set_state(Form.start_time); await message.answer("🕐 **Укажите время:**",reply_markup=time_kb(),parse_mode="Markdown"); return
    if w=="reason": await state.update_data(reason=text,waiting=None); await preview(message,state); return
    if cur==Form.start_time.state:
        if not valid_time(text): await message.answer("⚠️ Введите время как ЧЧ:ММ, например 08:30."); return
        await set_start_time(text,message,state); return
    if cur==Form.end_time.state:
        if not valid_time(text): await message.answer("⚠️ Введите время как ЧЧ:ММ, например 09:45."); return
        await state.update_data(end_time=text); await state.set_state(Form.reason); await message.answer("❓ **Выберите причину остановки:**",reply_markup=reason_kb(),parse_mode="Markdown"); return
    if cur==Form.quantity.state:
        if not text.isdigit(): await message.answer("⚠️ Введите количество целым числом, например 8280."); return
        await state.update_data(quantity=int(text)); await preview(message,state); return
    await message.answer("Нажмите /start, чтобы открыть меню.")

async def confirm(call:CallbackQuery,state:FSMContext,bot:Bot):
    if call.data.endswith(":no"): await state.clear(); await call.message.edit_text("❌ Запись отменена.",reply_markup=main_kb()); await call.answer(); return
    d=await state.get_data(); target=WORK_CHAT_ID or call.message.chat.id
    await bot.send_message(target,build_text(d),parse_mode="Markdown"); await state.clear(); await call.message.edit_text("✅ **Запись опубликована.**",reply_markup=main_kb(),parse_mode="Markdown"); await call.answer()

async def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not set")
    bot=Bot(BOT_TOKEN); dp=Dispatcher()
    dp.message.register(menu,Command("start")); dp.message.register(menu,Command("menu")); dp.message.register(menu,Command("отчет"))
    dp.callback_query.register(event,F.data.startswith("event:")); dp.callback_query.register(equipment,F.data.startswith("equipment:")); dp.callback_query.register(product,F.data.startswith("product:")); dp.callback_query.register(time_choice,F.data.startswith("time:")); dp.callback_query.register(end_time_choice,F.data.startswith("endtime:")); dp.callback_query.register(reason,F.data.startswith("reason:")); dp.callback_query.register(confirm,F.data.startswith("confirm:")); dp.message.register(text_input)
    logging.info("Production bot started; work_chat_id=%s; timezone=%s",WORK_CHAT_ID,TIMEZONE)
    await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
