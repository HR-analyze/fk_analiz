import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WORK_CHAT_ID = int(os.getenv("WORK_CHAT_ID", "0"))
TIMEZONE = ZoneInfo("Europe/Moscow")

EQUIPMENT = ["Кёниг 1", "Кёниг 2", "Рондо 1", "Рондо 2"]
PRODUCTS = ["Бейгл 120г", "Багет злаковый 250г", "Пирожки"]
STOP_REASONS = [
    "Подготовка форм",
    "Переналадка",
    "Поломка",
    "Нет сырья",
    "Нет персонала",
    "Санитарная обработка",
]

class ProductionForm(StatesGroup):
    event_type = State()
    equipment = State()
    product = State()
    start_time = State()
    end_time = State()
    quantity = State()
    reason = State()
    preview = State()


def menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🟢 Старт производства", callback_data="event:start")
    b.button(text="🔴 Стоп производства", callback_data="event:stop")
    b.button(text="⏸ Остановка", callback_data="event:pause")
    b.adjust(1)
    return b.as_markup()


def equipment_kb():
    b = InlineKeyboardBuilder()
    for i, item in enumerate(EQUIPMENT):
        b.button(text=item, callback_data=f"equipment:{i}")
    b.button(text="Другое", callback_data="equipment:other")
    b.adjust(2)
    return b.as_markup()


def product_kb():
    b = InlineKeyboardBuilder()
    for i, item in enumerate(PRODUCTS):
        b.button(text=item, callback_data=f"product:{i}")
    b.button(text="Другое", callback_data="product:other")
    b.adjust(2)
    return b.as_markup()


def time_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🕐 Сейчас", callback_data="time:now")
    b.button(text="⌨️ Ввести время", callback_data="time:manual")
    b.adjust(1)
    return b.as_markup()


def reason_kb():
    b = InlineKeyboardBuilder()
    for i, item in enumerate(STOP_REASONS):
        b.button(text=item, callback_data=f"reason:{i}")
    b.button(text="Другая", callback_data="reason:other")
    b.adjust(2)
    return b.as_markup()


def confirm_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✅ Опубликовать", callback_data="confirm:yes")
    b.button(text="❌ Отмена", callback_data="confirm:no")
    b.adjust(1)
    return b.as_markup()


def format_time(value: str) -> str:
    return value


def build_text(data: dict) -> str:
    event = data["event_type"]
    equipment = data["equipment"]
    product = data["product"]
    if event == "start":
        return f"🟢 **Старт {equipment} – {product} | Старт: {data['start_time']}**"
    if event == "stop":
        return f"🔴 **Стоп {equipment} – {product} | Стоп: {data['start_time']} | Количество: {data['quantity']} шт**"
    return f"⏸ **Остановка {equipment} – {product} | с {data['start_time']} до {data['end_time']} | Причина: {data['reason']}**"


async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📊 **Производство**\n\nВыберите действие:", reply_markup=menu_kb(), parse_mode="Markdown")


async def event_selected(call: CallbackQuery, state: FSMContext):
    event = call.data.split(":", 1)[1]
    await state.update_data(event_type=event)
    await state.set_state(ProductionForm.equipment)
    await call.message.edit_text("🏭 Выберите оборудование:", reply_markup=equipment_kb())
    await call.answer()


async def equipment_selected(call: CallbackQuery, state: FSMContext):
    value = call.data.split(":", 1)[1]
    if value == "other":
        await state.set_state(ProductionForm.equipment)
        await state.update_data(waiting_equipment=True)
        await call.message.answer("Напишите название оборудования:")
    else:
        await state.update_data(equipment=EQUIPMENT[int(value)], waiting_equipment=False)
        await state.set_state(ProductionForm.product)
        await call.message.edit_text("🥐 Выберите продукт:", reply_markup=product_kb())
    await call.answer()


async def product_selected(call: CallbackQuery, state: FSMContext):
    value = call.data.split(":", 1)[1]
    if value == "other":
        await state.update_data(waiting_product=True)
        await call.message.answer("Напишите название продукта:")
    else:
        await state.update_data(product=PRODUCTS[int(value)], waiting_product=False)
        data = await state.get_data()
        await state.set_state(ProductionForm.start_time)
        if data["event_type"] == "pause":
            await call.message.edit_text("🕐 Укажите время начала остановки:", reply_markup=time_kb())
        else:
            await call.message.edit_text("🕐 Укажите время:", reply_markup=time_kb())
    await call.answer()


async def time_selected(call: CallbackQuery, state: FSMContext):
    value = call.data.split(":", 1)[1]
    if value == "now":
        now = datetime.now(TIMEZONE).strftime("%H:%M")
        await state.update_data(start_time=now)
        data = await state.get_data()
        if data["event_type"] == "pause":
            await state.set_state(ProductionForm.end_time)
            await call.message.edit_text("🕐 Укажите время окончания остановки:", reply_markup=time_kb())
        elif data["event_type"] == "stop":
            await state.set_state(ProductionForm.quantity)
            await call.message.edit_text("🔢 Укажите количество произведено (шт):")
        else:
            await show_preview(call.message, state)
    else:
        await call.message.answer("Введите время в формате ЧЧ:ММ, например 08:30:")
    await call.answer()


async def show_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.set_state(ProductionForm.preview)
    await message.answer("Проверьте запись:\n\n" + build_text(data), reply_markup=confirm_kb(), parse_mode="Markdown")


async def text_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    current = await state.get_state()
    text = message.text.strip()
    if data.get("waiting_equipment"):
        await state.update_data(equipment=text, waiting_equipment=False)
        await state.set_state(ProductionForm.product)
        await message.answer("🥐 Выберите продукт:", reply_markup=product_kb())
        return
    if data.get("waiting_product"):
        await state.update_data(product=text, waiting_product=False)
        await state.set_state(ProductionForm.start_time)
        await message.answer("🕐 Укажите время:", reply_markup=time_kb())
        return
    if current == ProductionForm.start_time.state:
        try:
            datetime.strptime(text, "%H:%M")
        except ValueError:
            await message.answer("Неверный формат. Введите время как ЧЧ:ММ, например 08:30.")
            return
        await state.update_data(start_time=text)
        if data["event_type"] == "pause":
            await state.set_state(ProductionForm.end_time)
            await message.answer("🕐 Укажите время окончания:", reply_markup=time_kb())
        elif data["event_type"] == "stop":
            await state.set_state(ProductionForm.quantity)
            await message.answer("🔢 Укажите количество произведено (шт):")
        else:
            await show_preview(message, state)
        return
    if current == ProductionForm.end_time.state:
        try:
            datetime.strptime(text, "%H:%M")
        except ValueError:
            await message.answer("Неверный формат. Введите время как ЧЧ:ММ, например 09:45.")
            return
        await state.update_data(end_time=text)
        await state.set_state(ProductionForm.reason)
        await message.answer("❓ Выберите причину остановки:", reply_markup=reason_kb())
        return
    if current == ProductionForm.quantity.state:
        if not text.isdigit():
            await message.answer("Введите количество целым числом, например 8280.")
            return
        await state.update_data(quantity=int(text))
        await show_preview(message, state)
        return
    if current == ProductionForm.reason.state:
        await state.update_data(reason=text)
        await show_preview(message, state)
        return
    if current == ProductionForm.preview.state:
        await message.answer("Используйте кнопки подтверждения ниже.")


async def reason_selected(call: CallbackQuery, state: FSMContext):
    value = call.data.split(":", 1)[1]
    if value == "other":
        await call.message.answer("Напишите причину остановки:")
    else:
        await state.update_data(reason=STOP_REASONS[int(value)])
        await show_preview(call.message, state)
    await call.answer()


async def confirm_selected(call: CallbackQuery, state: FSMContext, bot: Bot):
    value = call.data.split(":", 1)[1]
    if value == "no":
        await state.clear()
        await call.message.edit_text("❌ Запись отменена.", reply_markup=menu_kb())
        await call.answer()
        return
    data = await state.get_data()
    text = build_text(data)
    target = WORK_CHAT_ID or call.message.chat.id
    await bot.send_message(target, text, parse_mode="Markdown")
    await state.clear()
    await call.message.edit_text("✅ Запись опубликована.", reply_markup=menu_kb())
    await call.answer()


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(start_cmd, Command("start"))
    dp.message.register(start_cmd, Command("отчет"))
    dp.callback_query.register(event_selected, F.data.startswith("event:"))
    dp.callback_query.register(equipment_selected, F.data.startswith("equipment:"))
    dp.callback_query.register(product_selected, F.data.startswith("product:"))
    dp.callback_query.register(time_selected, F.data.startswith("time:"))
    dp.callback_query.register(reason_selected, F.data.startswith("reason:"))
    dp.callback_query.register(confirm_selected, F.data.startswith("confirm:"))
    dp.message.register(text_handler)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
