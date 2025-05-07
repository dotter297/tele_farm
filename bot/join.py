from aiogram import Router, types, Bot, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import os
from telethon import TelegramClient
from telethon.errors import (UserAlreadyParticipantError, AuthKeyUnregisteredError,
                              UserBannedInChannelError, InviteRequestSentError,
                              ChatWriteForbiddenError, FloodWaitError)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from sqlalchemy.future import select
from db.sessions import get_db
from db.models import TelegramSession, Flow
from bot.logger import logger
import random
from aiogram.types import ReplyKeyboardMarkup,KeyboardButton

router = Router()
SESSIONS_PATH = "sessions/"

class JoinMenuStates(StatesGroup):
    main_menu = State()
    waiting_for_link = State()
    waiting_for_interval = State()
    waiting_for_activity = State()  # Новый шаг для задания активности

def sessions_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Ввести ссылку")],
            [types.KeyboardButton(text="⏳ Установить интервал подписки")],
            [types.KeyboardButton(text="⏰ Установить время активности")],
            [types.KeyboardButton(text="⚡️ Переключить многопоточный режим")],
            [types.KeyboardButton(text="▶️ Начать подписку")],
            [types.KeyboardButton(text="⛔️ Остановить подписку")],
            [types.KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👥 Сессии")],[KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📩 Подписаться на группу"), KeyboardButton(text="🚫 Выйти из группы")],
        [KeyboardButton(text="📢 Проверить подписку"), KeyboardButton(text="📨 Начать рассылку")],
        [KeyboardButton(text="🛠 Админ-панель"),KeyboardButton(text="🌐 Управление прокси")]
    ],
    resize_keyboard=True
)

def get_menu_keyboard():
    return sessions_keyboard()

@router.message(F.text=="🔙 Назад")
async def back_to_main_from_subscription(message: types.Message):
    await message.answer("Главное меню", reply_markup=main_keyboard)

@router.message(Command("join_menu"))
async def join_menu(message: types.Message, state: FSMContext):
    await state.set_state(JoinMenuStates.main_menu)
    await state.update_data(multi_threading=False, link="не установлена", interval=0, active_start=None, active_end=None)
    text = ("📩 Меню подписки:\n"
            "Текущая ссылка: не установлена\n"
            "Интервал: 0 сек\n"
            "Многопоточный режим: выключен\n"
            "Время активности: не установлено\n"
            "Выберите действие:")
    await message.answer(text, reply_markup=get_menu_keyboard())    

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "➕ Ввести ссылку")
async def set_subscription_link(message: types.Message, state: FSMContext):
    await message.answer("Введите ссылку на группу:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(JoinMenuStates.waiting_for_link)

@router.message(JoinMenuStates.waiting_for_link)
async def process_link(message: types.Message, state: FSMContext):
    await state.update_data(link=message.text)
    await message.answer(f"✅ Ссылка установлена: {message.text}", reply_markup=get_menu_keyboard())
    await state.set_state(JoinMenuStates.main_menu)

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "⚡️ Переключить многопоточный режим")
async def toggle_multithreading(message: types.Message, state: FSMContext):
    data = await state.get_data()
    multi_threading = not data.get("multi_threading", False)
    await state.update_data(multi_threading=multi_threading)
    status = "включен" if multi_threading else "выключен"
    await message.answer(
        f"⚡️ Многопоточный режим {status}\n\n"
        "📈 Вы можете настроить потоки в разделе 'Статистика'.",
        reply_markup=get_menu_keyboard()
    )


@router.message(JoinMenuStates.main_menu, lambda message: message.text == "▶️ Начать подписку")
async def start_subscription(message: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get("link")
    min_delay = data.get("min_delay", 5 * 60)  # По умолчанию 5 минут
    max_delay = data.get("max_delay", 10 * 60)  # По умолчанию 10 минут

    if not link or link == "не установлена":
        await message.answer("❌ Ссылка на группу не установлена.")
        return

    task = asyncio.create_task(join_group(message, link, min_delay, max_delay, state))
    tasks = data.get("joining_tasks", [])
    tasks.append(task)
    await state.update_data(joining_tasks=tasks)

@router.message(lambda message: message.text == "📩 Подписаться на группу")
async def open_join_menu(message: types.Message, state: FSMContext):
    await join_menu(message, state)

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "⏳ Установить интервал подписки")
async def set_subscription_interval(message: types.Message, state: FSMContext):
    await message.answer(
        "Введите минимальный и максимальный интервал подписки в минутах через пробел (например, 5 10):",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(JoinMenuStates.waiting_for_interval)

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "⛔️ Остановить подписку")
async def stop_joining(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tasks = data.get("joining_tasks", [])
    if tasks:
        for task in tasks:
            if not task.done() and not task.cancelled():
                task.cancel()
        await state.update_data(joining_tasks=[])
        await message.answer("⛔️ Подписка остановлена.", reply_markup=get_menu_keyboard())
    else:
        await message.answer("⚠️ Нет активной подписки.")

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "⏳ Установить интервал подписки")
async def set_subscription_interval(message: types.Message, state: FSMContext):
    await message.answer("Введите минимальный и максимальный интервал подписки в минутах через пробел (например, 5 10):",
                         reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(JoinMenuStates.waiting_for_interval)

@router.message(JoinMenuStates.waiting_for_interval)
async def process_interval(message: types.Message, state: FSMContext):
    try:
        min_delay, max_delay = map(int, message.text.split())
        if min_delay > max_delay or min_delay <= 0 or max_delay <= 0:
            raise ValueError
        await state.update_data(min_delay=min_delay * 60, max_delay=max_delay * 60)
        await message.answer(f"✅ Интервал подписки установлен: от {min_delay} до {max_delay} минут", reply_markup=get_menu_keyboard())
        await state.set_state(JoinMenuStates.main_menu)
    except ValueError:
        await message.answer("⚠️ Введите два корректных числа через пробел, например: 5 10")

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "⏰ Установить время активности")
async def set_activity_time(message: types.Message, state: FSMContext):
    await message.answer("Введите время активности в формате HH:MM-HH:MM (например, 09:00-18:00):",
                         reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(JoinMenuStates.waiting_for_activity)

@router.message(JoinMenuStates.waiting_for_activity)
async def process_activity_time(message: types.Message, state: FSMContext):
    try:
        active_time = message.text.strip()
        start_time, end_time = active_time.split("-")
        for time in (start_time, end_time):
            hours, minutes = map(int, time.split(":"))
            if not (0 <= hours < 24 and 0 <= minutes < 60):
                raise ValueError("Некорректное время")
        await state.update_data(active_start=start_time, active_end=end_time)
        await message.answer(f"✅ Время активности установлено: с {start_time} до {end_time}", reply_markup=get_menu_keyboard())
        await state.set_state(JoinMenuStates.main_menu)
    except ValueError:
        await message.answer("⚠️ Некорректный формат времени. Введите в формате HH:MM-HH:MM (например, 09:00-18:00).")

@router.message(JoinMenuStates.main_menu, lambda message: message.text == "Управление потоками")
async def manage_flows(message: types.Message, state: FSMContext):
    # Получаем потоки пользователя из базы
    async for db in get_db():
        result = await db.execute(select(Flow).where(Flow.user_id == message.from_user.id))
        flows = result.scalars().all()
    if not flows:
        await message.answer("У вас еще нет созданных потоков.", reply_markup=get_menu_keyboard())
        return
    # Формируем inline-клавиатуру: каждая кнопка отображает имя потока и содержит его ID в callback_data
    inline_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=flow.name, callback_data=f"select_flow:{flow.id}")]
        for flow in flows
    ])
    inline_kb.inline_keyboard.append(
        [types.InlineKeyboardButton(text="🔙 В главное меню", callback_data="session_menu_back")]
    )
    await message.answer("Выберите поток для управления:", reply_markup=inline_kb)

@router.callback_query(F.data == "session_menu_back")
async def back_to_main_menu_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🔙 Главное меню", reply_markup=get_menu_keyboard())
    await callback.answer()

@router.message(F.text == "⬅ Назад")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    from bot.handlers import main_keyboard
    await state.set_state(State())
    await message.answer("🔙 Возвращаюсь в главное меню", reply_markup=main_keyboard)

async def join_group(message, group_link, min_delay, max_delay, state: FSMContext):
    successful_joins = 0
    failed_joins = 0

    if not group_link.startswith("https://t.me/"):
        await message.answer(
            "❌ Введите корректную ссылку на Telegram-группу (пример: https://t.me/example_channel)"
        )
        return

    chat = await message.bot.get_chat(message.chat.id)
    data = await state.get_data()
    multi_threading = data.get("multi_threading", False)

    await message.answer("🔍 Начинаю подписку аккаунтов...")

    async for db in get_db():
        sessions_result = await db.execute(select(TelegramSession))
        sessions = sessions_result.scalars().all()
        if not sessions:
            await message.answer("⚠️ Нет доступных аккаунтов для подписки.")
            return

        semaphore = asyncio.Semaphore(3)

        async def join_single_account(session):
            async with semaphore:
                session_file_path = os.path.join(SESSIONS_PATH, session.session_file)
                if not os.path.exists(session_file_path):
                    await message.answer(
                        f"🚫 Файл сессии `{session.session_file}` не найден, пропускаем."
                    )
                    return

                client = TelegramClient(session_file_path, session.api_id, session.api_hash)
                await client.connect()

                if not await client.is_user_authorized():
                    try:
                        logger.warning(
                            f"🔄 Аккаунт {session.user_id} не активен. Пробуем повторную авторизацию..."
                        )
                        await client.start()
                    except Exception as e:
                        await db.execute(
                            TelegramSession.__table__.delete().where(
                                TelegramSession.user_id == session.user_id
                            )
                        )
                        await db.commit()
                        await message.answer(
                            f"🚫 Сессия `{session.session_file}` устарела и удалена из базы."
                        )
                        await client.disconnect()
                        return

                try:
                    if "/+" in group_link:
                        invite_hash = group_link.split("/+")[-1]
                        await client(ImportChatInviteRequest(invite_hash))
                    else:
                        await client(JoinChannelRequest(group_link))


                    await message.answer(
                        f"✅ Аккаунт {session.user_id} подписался на {group_link}"
                    )
                    nonlocal successful_joins
                    successful_joins += 1
                except UserAlreadyParticipantError:
                    await message.answer(
                        f"ℹ️ Аккаунт {session.user_id} уже подписан на {group_link}"
                    )
                except UserBannedInChannelError:
                    await message.answer(
                        f"🚫 Аккаунт {session.user_id} заблокирован в {group_link}, пропускаем."
                    )
                except InviteRequestSentError:
                    await message.answer(
                        f"📩 Аккаунт {session.user_id} отправил запрос на вступление в {group_link} (закрытая группа)."
                    )
                except ChatWriteForbiddenError:
                    await message.answer(
                        f"🚫 Аккаунт {session.user_id} не может писать в {group_link}, подписка невозможна."
                    )
                except FloodWaitError as e:
                    await message.answer(
                        f"⚠️ Telegram временно заблокировал подписку для аккаунта {session.user_id}. Ждём {e.seconds} секунд..."
                    )
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    await message.answer(f"❌ Ошибка у аккаунта {session.user_id}: {e}")
                    nonlocal failed_joins
                    failed_joins += 1
                finally:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    await asyncio.sleep(random.randint(min_delay, max_delay))

        if multi_threading:
            chunk_size = 3
            tasks = []
            for i in range(0, len(sessions), chunk_size):
                chunk = sessions[i:i + chunk_size]
                tasks.append(
                    asyncio.gather(*[join_single_account(session) for session in chunk])
                )
            await asyncio.gather(*tasks)
        else:
            for session in sessions:
                await join_single_account(session)

    await message.answer(
        f"📊 Подписка завершена:\n✅ Успешно: {successful_joins}\n❌ Ошибки: {failed_joins}"
    )
    logger.info(f"Подписка завершена: {successful_joins} успешных, {failed_joins} ошибок")
