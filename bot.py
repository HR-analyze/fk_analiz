import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
WORK_CHAT_ID_RAW = os.getenv("WORK_CHAT_ID", "").strip()
WORK_CHAT_ID = int(WORK_CHAT_ID_RAW) if WORK_CHAT_ID_RAW else None

EQUIPMENT = ["cromaster", "starline", "Glimek", "König / König хлеб", "Rondo", "Trima"]
FORMING = {
    "Холодная формовка": [
        "Булочка бриошь зерновая", "Булочка ржаная", "Булочка с корицей", "Булочка Сладкое сердце",
        "Венгерская ватрушка", "Круассан для сэндвича", "Круассан классика мини 55 г", "Круассан мини 50 г",
        "Круассан французский 70 г", "Круассан французский без дефроста", "Круассан французский с сыром",
        "Лепёшка сдобная", "Лепёшка сдобная с сосиской", "Начинка булочка с корицей",
        "Начинка для пирожков с курицей и сыром", "Начинка маковая для улитки", "Основа для слойки с вишней и заварным кремом",
        "Пирожок с курицей и сыром", "Рогалик вишнёвый", "Слойка голландская", "Слойка с вишней и заварным кремом",
        "Слойка с марсельской сосиской", "Слойка шоколад-апельсин", "Творожные ушки", "Трубочка",
        "Улитка с изюмом", "Улитка с маком", "Хачапури", "Хлеб Бородинский"
    ],
    "Тёплая формовка": [
        "Багет злаковый", "Багет молочный", "Багет ремесленный на опаре", "Багет сырный", "Батон классический",
        "Бейгл с кунжутом", "Булка Много мака", "Булочка для гамбургера без кунжута", "Булочка для супа тёмная",
        "Булочка для френч-дога", "Булочка для хот-дога белая", "Булочка с маком", "Булочка суповая светлая",
        "Мини-чиабатта", "Краюшки", "Пирожок с капустой фреш", "Пирожок с мясом фреш", "Ромовая баба",
        "Сочник с творогом", "Хлеб бездрожжевой с семечками", "Хлеб злаковый", "Хлеб картофельный", "Хлеб кефирный",
        "Хлеб протеиновый", "Хлеб пшеничный домашний", "Хлеб с семенами чиа и пажитником", "Хлеб тартин ржано-пшеничный",
        "Хлеб тартин розовый", "Хлеб тостовый молочный", "Хлеб тостовый слоёный фреш", "Хлеб тыквенный",
        "Хлеб чесночный", "Чиабатта пшеничная смесевая"
    ]
}
REASONS = ["Поломка оборудования", "Нет сырья", "Нет персонала", "Техническая проблема", "Качество продукции"]

class Form(StatesGroup):
    equipment = State()
    forming = State()
    product = State()
    time = State()
    quantity = State()
    reason = State()
    pause_start = State()
    pause_end = State()
    confirm = State()
    custom_equipment = State()
    custom_reason = State()


def keyboard(items, columns=1):
    b = InlineKeyboardBuilder()
    for text, data in items:
        b.button(text=text, callback_data=data)
    b.adjust(columns)
    return b.as_markup()


def now():
    return datetime.now(TIMEZONE).strftime("%H:%M")


def valid_time(value):
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def action_title(event):
    return {"start": "🟢 Начало производства", "finish": "🏁 Завершение производства", "pause": "🔴 Критическая остановка"}[event]


def result_text(d):
    event = d["event"]
    if event == "start":
        return (f"🟢 **Начало производства**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n"
                f"📦 {d['product']}\n🕐 Начало: {d['time']}")
    if event == "finish":
        return (f"🏁 **Завершение производства**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n"
                f"📦 {d['product']}\n🕐 Завершение: {d['time']}\n🔢 Количество: {d['quantity']:,} шт".replace(",", " "))
    return (f"🔴 **Критическая остановка**\n👤 {d['user_name']}\n🏭 {d['equipment']}\n"
            f"❗ Причина: {d['reason']}\n🕐 Период: {d['pause_start']}–{d['pause_end']}")


async def start_command(message: Message, command: CommandObject, state: FSMContext):
    if message.chat.type in ("group", "supergroup"):
        await send_panel(message)
        return
    if message.chat.type != "private":
        return
    payload = (command.args or "").strip()
    if not payload.startswith("production_"):
        await message.answer("📊 Запустите операцию через закреплённую панель рабочего чата.")
        return
    event = payload.removeprefix("production_")
    if event not in ("start", "finish", "pause"):
        await message.answer("Неизвестная операция.")
        return
    await state.clear()
    await state.update_data(event=event, user_name=message.from_user.full_name)
    await state.set_state(Form.equipment)
    await message.answer("🏭 **Выберите оборудование:**", reply_markup=keyboard([(x, f"q:{i}") for i, x in enumerate(EQUIPMENT)] + [("✏️ Другое", "q:other")], 2), parse_mode="Markdown")


async def send_panel(message: Message):
    sent = await message.answer(
        "📌 **ФК — производство**\n\nВыберите действие. Заполнение продолжится в личном чате с ботом.",
        reply_markup=keyboard([
            ("🟢 Начало производства", "e:start"),
            ("🏁 Завершение производства", "e:finish"),
            ("🔴 Критическая остановка", "e:pause")
        ]), parse_mode="Markdown"
    )
    try:
        await message.bot.pin_chat_message(message.chat.id, sent.message_id, disable_notification=True)
    except Exception:
        logging.warning("Could not pin panel; bot may need admin rights")


async def setup_command(message: Message):
    if message.chat.type in ("group", "supergroup"):
        await send_panel(message)


async def callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data or ""
    try:
        if data.startswith("e:"):
            event = data[2:]
            if callback.message.chat.type not in ("group", "supergroup"):
                await callback.answer("Запустите операцию из рабочего чата.", show_alert=True)
                return
            bot_info = await callback.bot.get_me()
            url = f"https://t.me/{bot_info.username}?start=production_{event}"
            await callback.message.answer(
                f"👤 **{callback.from_user.full_name}**, форма готова.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="➡️ Открыть форму", url=url)
                ]]), parse_mode="Markdown"
            )
            await callback.answer()
            return

        if data.startswith("q:"):
            value = data[2:]
            if value == "other":
                await state.set_state(Form.custom_equipment)
                await callback.message.edit_text("🏭 Напишите название оборудования:")
                return
            await state.update_data(equipment=EQUIPMENT[int(value)])
            d = await state.get_data()
            if d["event"] == "pause":
                await state.set_state(Form.reason)
                await callback.message.edit_text("❗ **Выберите причину остановки:**", reply_markup=keyboard([(x, f"r:{i}") for i, x in enumerate(REASONS)] + [("✏️ Другая причина", "r:other")]), parse_mode="Markdown")
            else:
                await state.set_state(Form.forming)
                await callback.message.edit_text("📦 **Выберите вид формовки:**", reply_markup=keyboard([(x, f"g:{i}") for i, x in enumerate(FORMING)]), parse_mode="Markdown")
            return

        if data.startswith("g:"):
            group = list(FORMING)[int(data[2:])]
            await state.update_data(forming=group)
            await state.set_state(Form.product)
            await callback.message.edit_text("🔎 **Напишите несколько букв продукции.**\nНапример: `хлеб`, `бейгл`, `пирожок`.", parse_mode="Markdown")
            return

        if data.startswith("p:"):
            d = await state.get_data()
            results = d.get("results", [])
            value = data[2:]
            product = "Другое / нет в справочнике" if value == "other" else results[int(value)]
            await state.update_data(product=product)
            await state.set_state(Form.time)
            await callback.message.edit_text("🕐 **Укажите время:**", reply_markup=keyboard([("🕐 Сейчас", "t:now"), ("⌨️ Ввести время", "t:manual")]), parse_mode="Markdown")
            return

        if data.startswith("t:"):
            if data[2:] == "manual":
                await state.set_state(Form.time)
                await callback.message.edit_text("⌨️ Напишите время в формате `08:30`.", parse_mode="Markdown")
            else:
                await state.update_data(time=now())
                d = await state.get_data()
                if d["event"] == "finish":
                    await state.set_state(Form.quantity)
                    await callback.message.edit_text("🔢 **Сколько штук произвели?**\nНапример: `8280`", parse_mode="Markdown")
                else:
                    await show_confirmation(callback.message, state)
            return

        if data.startswith("r:"):
            value = data[2:]
            if value == "other":
                await state.set_state(Form.custom_reason)
                await callback.message.edit_text("❗ Напишите причину остановки:")
            else:
                await state.update_data(reason=REASONS[int(value)])
                await state.set_state(Form.pause_start)
                await callback.message.edit_text("🕐 **Когда началась остановка?**", reply_markup=keyboard([("🕐 Сейчас", "ps:now"), ("⌨️ Ввести время", "ps:manual")]), parse_mode="Markdown")
            return

        if data.startswith("ps:"):
            if data[3:] == "manual":
                await state.set_state(Form.pause_start)
                await callback.message.edit_text("⌨️ Введите время начала, например `12:10`.", parse_mode="Markdown")
            else:
                await state.update_data(pause_start=now())
                await state.set_state(Form.pause_end)
                await callback.message.edit_text("🕐 **Когда закончилась остановка?**", reply_markup=keyboard([("🕐 Сейчас", "pe:now"), ("⌨️ Ввести время", "pe:manual")]), parse_mode="Markdown")
            return

        if data.startswith("pe:"):
            if data[3:] == "manual":
                await state.set_state(Form.pause_end)
                await callback.message.edit_text("⌨️ Введите время окончания, например `12:47`.", parse_mode="Markdown")
            else:
                await state.update_data(pause_end=now())
                await show_confirmation(callback.message, state)
            return

        if data == "c:yes":
            d = await state.get_data()
            if not WORK_CHAT_ID:
                await callback.answer("WORK_CHAT_ID не задан", show_alert=True)
                return
            await callback.bot.send_message(WORK_CHAT_ID, result_text(d), parse_mode="Markdown")
            await state.clear()
            await callback.message.edit_text("✅ **Готово! Итог опубликован в рабочем чате.**", parse_mode="Markdown")
            return
        if data == "c:no":
            await state.clear()
            await callback.message.edit_text("❌ Запись отменена.")
            return
    except Exception:
        logging.exception("Callback failed: %s", data)
        await callback.answer("Ошибка. Попробуйте ещё раз.", show_alert=True)
        return
    await callback.answer()


async def show_confirmation(message: Message, state: FSMContext):
    d = await state.get_data()
    await state.set_state(Form.confirm)
    await message.edit_text("**Проверьте запись:**\n\n" + result_text(d), reply_markup=keyboard([("✅ Всё верно — опубликовать", "c:yes"), ("❌ Отмена", "c:no")]), parse_mode="Markdown")


async def text_input(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    current = await state.get_state()
    value = (message.text or "").strip()
    d = await state.get_data()
    try:
        if current == Form.custom_equipment.state:
            await state.update_data(equipment=value)
            d = await state.get_data()
            if d["event"] == "pause":
                await state.set_state(Form.reason)
                await message.answer("❗ **Выберите причину остановки:**", reply_markup=keyboard([(x, f"r:{i}") for i, x in enumerate(REASONS)] + [("✏️ Другая причина", "r:other")]), parse_mode="Markdown")
            else:
                await state.set_state(Form.forming)
                await message.answer("📦 **Выберите вид формовки:**", reply_markup=keyboard([(x, f"g:{i}") for i, x in enumerate(FORMING)]), parse_mode="Markdown")
            return

        if current == Form.custom_reason.state:
            await state.update_data(reason=value)
            await state.set_state(Form.pause_start)
            await message.answer("🕐 **Когда началась остановка?**", reply_markup=keyboard([("🕐 Сейчас", "ps:now"), ("⌨️ Ввести время", "ps:manual")]), parse_mode="Markdown")
            return

        if current == Form.product.state:
            if value.lower() in ("другое", "нет", "нет в списке"):
                await state.update_data(product="Другое / нет в справочнике")
            else:
                results = [p for p in FORMING[d["forming"]] if value.lower() in p.lower()]
                if not results:
                    await message.answer("😕 Не нашёл. Попробуйте проще или напишите **другое**.", parse_mode="Markdown")
                    return
                await state.update_data(results=results)
                await message.answer("🔎 **Выберите продукцию:**", reply_markup=keyboard([(p, f"p:{i}") for i, p in enumerate(results[:20])] + [("✏️ Другое / нет в списке", "p:other")]), parse_mode="Markdown")
                return
            await state.set_state(Form.time)
            await message.answer("🕐 **Укажите время:**", reply_markup=keyboard([("🕐 Сейчас", "t:now"), ("⌨️ Ввести время", "t:manual")]), parse_mode="Markdown")
            return

        if current == Form.time.state:
            if not valid_time(value):
                await message.answer("⚠️ Введите время в формате `08:30`.", parse_mode="Markdown")
                return
            await state.update_data(time=value)
            d = await state.get_data()
            if d["event"] == "finish":
                await state.set_state(Form.quantity)
                await message.answer("🔢 **Сколько штук произвели?**\nНапример: `8280`", parse_mode="Markdown")
            else:
                await show_confirmation(message, state)
            return

        if current == Form.quantity.state:
            clean = value.lower().replace(" ", "").replace("шт", "")
            if not clean.isdigit() or int(clean) <= 0:
                await message.answer("⚠️ Введите положительное количество цифрами, например `3000`.")
                return
            await state.update_data(quantity=int(clean))
            await show_confirmation(message, state)
            return

        if current == Form.pause_start.state:
            if not valid_time(value):
                await message.answer("⚠️ Введите время в формате `12:10`.", parse_mode="Markdown")
                return
            await state.update_data(pause_start=value)
            await state.set_state(Form.pause_end)
            await message.answer("🕐 **Когда закончилась остановка?**", reply_markup=keyboard([("🕐 Сейчас", "pe:now"), ("⌨️ Ввести время", "pe:manual")]), parse_mode="Markdown")
            return

        if current == Form.pause_end.state:
            if not valid_time(value):
                await message.answer("⚠️ Введите время в формате `12:47`.", parse_mode="Markdown")
                return
            await state.update_data(pause_end=value)
            await show_confirmation(message, state)
    except Exception:
        logging.exception("Text handler failed")
        await message.answer("⚠️ Не удалось обработать ответ. Попробуйте ещё раз.")


async def chatid(message: Message):
    if message.chat.type in ("group", "supergroup"):
        await message.answer(f"🆔 WORK_CHAT_ID:\n`{message.chat.id}`", parse_mode="Markdown")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.register(start_command, Command("start"))
    dp.message.register(setup_command, Command("setup_production"))
    dp.message.register(chatid, Command("chatid"))
    dp.callback_query.register(callbacks)
    dp.message.register(text_input)
    logging.info("Production bot started; work_chat_id=%s; timezone=%s", WORK_CHAT_ID, TIMEZONE)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
