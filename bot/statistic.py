from aiogram import Router, types, Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import os
import random
from datetime import datetime
from telethon import TelegramClient, functions, types as ttypes
from db.sessions import get_db
from db.models import TelegramSession, Flow
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload
import logging
from aiogram.types import CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from db.sessions import async_session

# Настройка логирования
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Инициализация роутера для регистрации хендлеров
router = Router()

# Путь к сессионным файлам
SESSIONS_PATH = "sessions/"

# ================================
# Функция поддержки онлайн-статуса для сессии
# ================================
async def keep_online(session):
    """
    Подключается к сессии Telethon, переводит сначала в offline, затем через секунду в online,
    и отправляет сообщение самому себе "Оставайся онлайн".
    Выполняется только для сессий с is_active == True.
    """
    session_file_path = os.path.join(SESSIONS_PATH, session.session_file)
    if not os.path.exists(session_file_path):
        print(f"Файл сессии не найден: {session_file_path}")
        return

    client = TelegramClient(session_file_path, session.api_id, session.api_hash)
    try:
        await client.connect()
        if await client.is_user_authorized():
            await client(functions.account.UpdateStatusRequest(offline=True))
            await asyncio.sleep(1)
            await client(functions.account.UpdateStatusRequest(offline=False))
            # Отправка сообщения самому себе
            await client.send_message("me", "Оставайся онлайн")
    except Exception as e:
        print(f"Ошибка в keep_online для сессии {session.id}: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()

# ================================
# Состояния для управления ботом
# ================================
class PollingStates(StatesGroup):
    main_menu = State()
    waiting_for_activity = State()
    selecting = State()
    waiting_for_save = State()

class FlowCreationStates(StatesGroup):
    waiting_for_sessions_count = State()
    waiting_for_sessions_selection = State()
    stop_poll = State()

# ================================
# Основное меню
# ================================
def get_menu_keyboard1():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Создать поток")],
            [types.KeyboardButton(text="📌 Статус сессий")],
            [types.KeyboardButton(text="⏰ Установить время активности")],
            [types.KeyboardButton(text="▶️ Запустить поллинг")],
            [types.KeyboardButton(text="▶️ Запустить глобальный поллинг")],
            [types.KeyboardButton(text="⛔ Остановить поллинг")],
            [types.KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👥 Сессии")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📩 Подписаться на группу"), KeyboardButton(text="🚫 Выйти из группы")],
        [KeyboardButton(text="📢 Проверить подписку"), KeyboardButton(text="📨 Начать рассылку")],
        [KeyboardButton(text="🛠 Админ-панель"), KeyboardButton(text="🌐 Управление прокси")]
    ],
    resize_keyboard=True
)

@router.message(F.text=="🔙 Назад")
async def back_to_main_from_subscription(message: types.Message):
    await message.answer("Главное меню", reply_markup=main_keyboard)

# ================================
# Функция пагинации сессий
# ================================
async def get_sessions_page(user_id: int, page: int, page_size: int = 2):
    """
    Получает сессии пользователя по страницам.
    Используется единый page_size по всем вызовам.
    """
    offset = page * page_size
    async with async_session() as session:
        result = await session.execute(
            select(TelegramSession)
            .where(TelegramSession.user_id == user_id)
            .offset(offset)
            .limit(page_size)
        )
        sessions = result.scalars().all()
        logger.info(f"get_sessions_page: offset={offset}, limit={page_size}, найдено {len(sessions)} сессий")
        return sessions

# ================================
# Хендлер: Статус сессий
# ================================
@router.message(F.text == "📌 Статус сессий")
async def show_sessions_status(message: types.Message, state: FSMContext):
    await state.update_data(page=0)
    await send_sessions_page(message, state, page=0)

async def send_sessions_page(message: types.Message, state: FSMContext, page: int = 0):
    async for db in get_db():
        sessions_result = await db.execute(
            select(TelegramSession).where(TelegramSession.user_id == message.from_user.id)
        )
        sessions = sessions_result.scalars().all()
    
    if not sessions:
        await message.answer("⚠️ У вас нет активных сессий.", reply_markup=get_menu_keyboard1())
        return

    sessions_per_page = 2
    total_sessions = len(sessions)
    max_page = (total_sessions - 1) // sessions_per_page
    
    page = max(0, min(page, max_page))
    start = page * sessions_per_page
    end = start + sessions_per_page
    paginated_sessions = sessions[start:end]
    
    text = "\ud83d\udccc Статус ваших сессий:\n\n"
    for session in paginated_sessions:
        session_file_path = os.path.join(SESSIONS_PATH, session.session_file)
        status = "\ud83d\udfe2 Активна" if os.path.exists(session_file_path) else "\ud83d\udd34 Заблокирована"
        text += f"ID: {session.id} | {status}\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prev_page:{page - 1}"))
    if page < max_page:
        buttons.append(InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"next_page:{page + 1}"))
    
    if buttons:
        keyboard.inline_keyboard.append(buttons)
    
    keyboard.inline_keyboard.append(
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="session_menu_back")]
    )
    
    await message.answer(text, reply_markup=keyboard)

@router.callback_query(F.data.startswith("prev_page:"))
async def previous_page_handler(callback_query: types.CallbackQuery, state: FSMContext):
    page = int(callback_query.data.split(":")[1])
    await callback_query.message.delete()
    await send_sessions_page(callback_query.message, state, page)

@router.callback_query(F.data.startswith("next_page:"))
async def next_page_handler(callback_query: types.CallbackQuery, state: FSMContext):
    page = int(callback_query.data.split(":")[1])
    await callback_query.message.delete()
    await send_sessions_page(callback_query.message, state, page)

@router.callback_query(F.data == "session_menu_back")
async def back_to_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.delete()
    await callback_query.message.answer("🔙 Главное меню", reply_markup=get_menu_keyboard1())

@router.message(F.text == "⛔ Остановить поллинг")
async def start_stop_polling(message: types.Message, state: FSMContext):
    streams = await get_session_status()  # Функция получает список активных потоков
    
    if not streams:
        await message.answer("⚠️ Нет активных потоков для остановки.")
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Поток #{stream['id']}", callback_data=f"stop_stream:{stream['id']}")]
            for stream in streams
        ]
    )
    
    await state.set_state(FlowCreationStates.stop_poll)
    await message.answer("🔴 Выберите поток для остановки:", reply_markup=keyboard)

@router.callback_query(lambda c: c.data.startswith("stop_stream:"))
async def stop_stream_callback(callback: types.CallbackQuery, state: FSMContext):
    stream_id = int(callback.data.split(":")[1])
    success = await stop_stream(stream_id)  # Функция останавливает поток
    
    if success:
        await callback.message.edit_text(f"✅ Поток #{stream_id} остановлен.")
    else:
        await callback.answer("⚠️ Не удалось остановить поток.", show_alert=True)
    
    await state.clear()
    await callback.answer()

@router.message(F.text == "➕ Создать поток")
async def start_flow_creation(message: types.Message, state: FSMContext):
    """
    Показывает количество доступных сессий и предлагает выбрать, сколько будет в потоке.
    """
    async for db in get_db():
        result = await db.execute(
            select(TelegramSession).where(TelegramSession.user_id == message.from_user.id)
        )
        sessions = result.scalars().all()
        total_sessions = len(sessions)

    if total_sessions == 0:
        await message.answer("⚠️ У вас нет активных сессий.", reply_markup=get_menu_keyboard1())
        return

    await state.set_state(FlowCreationStates.waiting_for_sessions_count)
    await message.answer(
        f"У вас {total_sessions} сессий. Сколько из них должно быть в потоке?",
        reply_markup=types.ReplyKeyboardRemove()
    )

@router.message(FlowCreationStates.waiting_for_sessions_count)
async def set_sessions_count(message: types.Message, state: FSMContext):
    """
    Устанавливает количество сессий в потоке, создает поток с выбранными сессиями
    и обновляет состояние для последующего использования в подписке.
    """
    input_text = message.text.strip()
    try:
        sessions_count = int(input_text)
        if sessions_count <= 0:
            raise ValueError("Число должно быть больше 0.")
    except ValueError:
        await message.answer("⚠️ Введите корректное число (целое и больше 0). Попробуйте ещё раз:")
        return

    async for db in get_db():
        result = await db.execute(
            select(TelegramSession).where(TelegramSession.user_id == message.from_user.id)
        )
        sessions = result.scalars().all()

        if len(sessions) < sessions_count:
            await message.answer(f"❌ Недостаточно сессий. Доступно только {len(sessions)}.")
            return

        # Выбираем случайным образом нужное количество сессий
        selected_sessions = random.sample(sessions, sessions_count)
        for session in selected_sessions:
            session.is_active = True  # Обновляем состояние сессии
        await db.commit()

        # Создаем поток с выбранными сессиями
        flow_name = f"Flow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        new_flow = Flow(name=flow_name, user_id=message.from_user.id, sessions=selected_sessions)
        db.add(new_flow)
        await db.commit()

        # Обновляем состояние для последующего использования в подписке:
        # сохраняем ID созданного потока в списке selected_flows
        await state.update_data(selected_flows=[new_flow.id])
        # Возвращаем пользователя в главное меню
        await state.set_state(PollingStates.main_menu)

        await message.answer(
            f"✅ Поток '{flow_name}' создан с {sessions_count} сессиями и выбран для подписки!",
            reply_markup=get_menu_keyboard1()
        )

@router.message(PollingStates.waiting_for_save, F.text == "✅ Сохранить")
async def save_flow_settings(message: types.Message, state: FSMContext):
    """
    Создаёт новый поток с выбранными сессиями.
    """
    data = await state.get_data()
    selected_sessions_ids = data.get("selected_sessions_ids", [])
    flow_name = f"Flow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async for db in get_db():
        result = await db.execute(
            select(TelegramSession).where(TelegramSession.id.in_(selected_sessions_ids))
        )
        selected_sessions = result.scalars().all()
        new_flow = Flow(name=flow_name, user_id=message.from_user.id, sessions=selected_sessions)
        db.add(new_flow)
        await db.commit()

    await state.clear()
    await message.answer(f"✅ Поток '{flow_name}' создан!", reply_markup=get_menu_keyboard1())

@router.message(PollingStates.waiting_for_save, F.text == "❌ Отмена")
async def cancel_flow_creation(message: types.Message, state: FSMContext):
    """
    Отменяет процесс создания потока.
    """
    await state.clear()
    await message.answer("⚠️ Создание потока отменено.", reply_markup=get_menu_keyboard1())

@router.callback_query(F.data.startswith("select_session_"))
async def select_session(callback: CallbackQuery, state: FSMContext):
    session_id = int(callback.data.split("_")[2])
    data = await state.get_data()
    selected_sessions_ids = data.get("selected_sessions_ids", [])
    
    # Добавляем текущую сессию в список выбранных, если она еще не была выбрана
    if session_id not in selected_sessions_ids:
        selected_sessions_ids.append(session_id)
        await state.update_data(selected_sessions_ids=selected_sessions_ids)

    sessions_count = data.get("sessions_count", 0)
    if sessions_count == 0:
        await callback.answer("⚠️ Не задано количество сессий для выбора.")
        return

    # Если выбрано достаточно сессий, создаем поток
    if len(selected_sessions_ids) == sessions_count:
        async for db in get_db():
            sessions_result = await db.execute(
                select(TelegramSession).where(TelegramSession.user_id == callback.from_user.id)
            )
            sessions = sessions_result.scalars().all()
            
            selected_sessions = [session for session in sessions if session.id in selected_sessions_ids]
            flow_name = f"Flow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            new_flow = Flow(name=flow_name, user_id=callback.from_user.id, sessions=selected_sessions)
            db.add(new_flow)
            await db.commit()

        await callback.message.answer(f"✅ Поток '{flow_name}' создан с {len(selected_sessions)} сессиями!", reply_markup=get_menu_keyboard1())
        await state.clear()
        return

    # Сообщаем пользователю о текущем статусе
    await callback.message.answer(f"Вы выбрали {len(selected_sessions_ids)} из {sessions_count} сессий.")
    await callback.answer()

# ================================
# Установка времени активности
# ================================
@router.message(F.text == "⏰ Установить время активности")
async def set_activity_time(message: types.Message, state: FSMContext):
    """
    Запрашивает время активности в формате HH:MM-HH:MM.
    """
    await message.answer(
        "Введите время активности в формате HH:MM-HH:MM (например, 09:00-18:00):",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(PollingStates.waiting_for_activity)

@router.message(PollingStates.waiting_for_activity)
async def process_activity_time(message: types.Message, state: FSMContext):
    """
    Обрабатывает введённое время активности.
    """
    try:
        active_time = message.text.strip()
        start_time, end_time = active_time.split("-")
        start_hour, start_minute = map(int, start_time.split(":"))
        end_hour, end_minute = map(int, end_time.split(":"))
        if not (0 <= start_hour < 24 and 0 <= start_minute < 60 and 0 <= end_hour < 24 and 0 <= end_minute < 60):
            raise ValueError("Некорректное время")
        
        await state.update_data(active_start=(start_hour, start_minute), active_end=(end_hour, end_minute))
        await message.answer(
            f"✅ Время активности установлено: с {start_time} до {end_time}",
            reply_markup=get_menu_keyboard1()
        )
        await state.set_state(PollingStates.main_menu)
    except ValueError:
        await message.answer(
            "⚠️ Некорректный формат времени. Введите в формате HH:MM-HH:MM (например, 09:00-18:00)."
        )

# ================================
# Функция бесконечного поллинга
# ================================
async def infinite_polling(active_start, active_end, sessions, message):
    """
    Бесконечный цикл поллинга, который для каждой сессии с is_active == True вызывает keep_online.
    """
    while True:
        now = datetime.now()
        start_dt = now.replace(hour=active_start[0], minute=active_start[1], second=0, microsecond=0)
        end_dt = now.replace(hour=active_end[0], minute=active_end[1], second=0, microsecond=0)
        
        if end_dt <= start_dt:
            if now < start_dt:
                pass
            else:
                end_dt = end_dt.replace(day=now.day + 1)
        
        if start_dt <= now <= end_dt:
            for session in sessions:
                if session.is_active:
                    asyncio.create_task(keep_online(session))
            await asyncio.sleep(60)
        else:
            if now < start_dt:
                delay = (start_dt - now).total_seconds()
            else:
                next_start = start_dt.replace(day=now.day + 1)
                delay = (next_start - now).total_seconds()
            await asyncio.sleep(delay)

# ================================
# Поллинг для выбранного потока и глобальный поллинг
# ================================
@router.message(F.text == "▶️ Запустить поллинг")
async def start_polling(message: types.Message, state: FSMContext):
    """
    Запускает поллинг для выбранного потока.
    """
    data = await state.get_data()
    selected_flow_id = data.get("selected_flow_id")

    async for db in get_db():
        keyboard = get_menu_keyboard1()
        if not keyboard:
            await message.answer("⚠️ У вас нет созданных потоков.")
            return

        if not selected_flow_id:
            await message.answer("📌 Выберите поток для поллинга:", reply_markup=keyboard)
            return

        result = await db.execute(
            select(Flow)
            .options(joinedload(Flow.sessions))
            .where(Flow.id == selected_flow_id)
        )
        # Используем unique() для корректного получения результата с joined eager load
        flow = result.unique().scalars().first()

        if not flow or not flow.sessions:
            await message.answer("⚠️ Поток не найден или в нём нет сессий.")
            return

        active_start = data.get("active_start")
        active_end = data.get("active_end")
        if not active_start or not active_end:
            await message.answer("⚠️ Время активности не установлено. Установите его через кнопку ⏰.")
            return

        # Запуск бесконечного поллинга для выбранного потока (только для сессий с is_active == True)
        task = asyncio.create_task(infinite_polling(active_start, active_end, flow.sessions, message))
        await state.update_data(polling_task=task)
        await message.answer("✅ Поллинг запущен для выбранного потока и будет работать постоянно.")

@router.message(F.text == "▶️ Запустить глобальный поллинг")
async def start_global_polling(message: types.Message, state: FSMContext):
    """
    Запускает глобальный поллинг для всех аккаунтов (сессий) пользователя,
    объединённых из всех созданных потоков.
    """
    data = await state.get_data()
    async for db in get_db():
        result = await db.execute(
            select(Flow)
            .options(joinedload(Flow.sessions))
            .where(Flow.user_id == message.from_user.id)
        )
        # Используем unique() для корректного получения результата
        flows = result.unique().scalars().all()

    if not flows:
        await message.answer("⚠️ У вас нет созданных потоков.")
        return

    all_sessions = []
    for flow in flows:
        if flow.sessions:
            all_sessions.extend(flow.sessions)
    if not all_sessions:
        await message.answer("⚠️ У вас нет активных сессий во всех потоках.")
        return

    active_start = data.get("active_start")
    active_end = data.get("active_end")
    if not active_start or not active_end:
        await message.answer("⚠️ Время активности не установлено. Установите его через кнопку ⏰.")
        return

    # Запуск глобального бесконечного поллинга для всех сессий (только для сессий с is_active == True)
    task = asyncio.create_task(infinite_polling(active_start, active_end, all_sessions, message))
    await state.update_data(global_polling_task=task)
    await message.answer("✅ Глобальный поллинг запущен для всех аккаунтов!")

@router.callback_query(F.data.startswith("select_flow_"))
async def process_flow_selection(callback: CallbackQuery, state: FSMContext):
    """
    Обрабатывает выбор потока для поллинга. Активирует выбранный поток и сохраняет его ID в состоянии.
    """
    flow_id = int(callback.data.split("_")[2])
    async for db in get_db():
        # Деактивируем все потоки пользователя
        await db.execute(
            select(Flow).where(Flow.user_id == callback.from_user.id).execution_options(synchronize_session="fetch")
        )
        flows = await db.execute(select(Flow).where(Flow.user_id == callback.from_user.id))
        for flow in flows.scalars().all():
            flow.is_active = False

        # Активируем выбранный поток
        result = await db.execute(select(Flow).where(Flow.id == flow_id))
        flow = result.unique().scalars().first()
        if not flow:
            await callback.message.answer("⚠️ Такой поток не найден.")
            return

        flow.is_active = True
        await db.commit()
        await state.update_data(selected_flow_id=flow_id)
        
        await callback.message.answer(
            f"✅ Поток '{flow.name}' активирован и теперь участвует в поллинге."
        )
        await callback.answer()

async def start_multi_polling(callback: CallbackQuery, state: FSMContext):
    """
    Запускает поллинг для нескольких потоков одновременно.
    """
    data = await state.get_data()
    selected_flows = data.get("selected_flows", [])

    if not selected_flows:
        await callback.answer("⚠️ Выберите хотя бы один поток!", show_alert=True)
        return

    async for db in get_db():
        flows_result = await db.execute(
            select(Flow)
            .options(joinedload(Flow.sessions))
            .where(Flow.id.in_(selected_flows))
        )
        flows = flows_result.unique().scalars().all()

    if not flows:
        await callback.message.answer("⚠️ Не удалось загрузить выбранные потоки.")
        return

    active_start = data.get("active_start")
    active_end = data.get("active_end")

    if not active_start or not active_end:
        await callback.message.answer("⚠️ Установите время активности перед запуском!")
        return

    # Запуск поллинга для каждого потока отдельно
    for flow in flows:
        asyncio.create_task(infinite_polling(active_start, active_end, flow.sessions, callback.message))

    await callback.message.answer(f"✅ Поллинг запущен для {len(flows)} потоков!", reply_markup=get_menu_keyboard1())
    await state.clear()

async def infinite_polling_for_flow(flow, active_start, active_end):
    """
    Бесконечный цикл поллинга для конкретного потока.
    Если текущее время входит в заданный интервал активности, для каждой активной сессии вызывается keep_online.
    """
    while True:
        now = datetime.now()
        start_dt = now.replace(hour=active_start[0], minute=active_start[1], second=0, microsecond=0)
        end_dt = now.replace(hour=active_end[0], minute=active_end[1], second=0, microsecond=0)

        if end_dt <= start_dt:
            if now < start_dt:
                pass
            else:
                end_dt = end_dt.replace(day=now.day + 1)

        if start_dt <= now <= end_dt:
            for session in flow.sessions:
                if session.is_active:
                    asyncio.create_task(keep_online(session))
            await asyncio.sleep(60)
        else:
            if now < start_dt:
                delay = (start_dt - now).total_seconds()
            else:
                next_start = start_dt.replace(day=now.day + 1)
                delay = (next_start - now).total_seconds()
            await asyncio.sleep(delay)
