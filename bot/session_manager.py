import re
import os
from aiogram import Router, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError, FloodWaitError, PhoneNumberInvalidError, PhoneCodeInvalidError
from sqlalchemy import select
from db.sessions import get_db
from db.models import TelegramSession, User, ProxySettings
from bot.logger import logger
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

router = Router()
UPLOAD_PATH = "sessions/"
os.makedirs(UPLOAD_PATH, exist_ok=True)

class SessionStates(StatesGroup):
    waiting_for_api_id = State()
    waiting_for_api_hash = State()
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

session_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Создать сессию")],
        [KeyboardButton(text="🔌 Применить прокси"), KeyboardButton(text="❌ Снять прокси")],
        [KeyboardButton(text="📂 Мои сессии")],
        [KeyboardButton(text="⬅ Назад")]
    ],
    resize_keyboard=True
)

# --- Функции для получения прокси и статуса сессии ---
async def get_proxy_tuple(state: FSMContext, user_id: int):
    """
    Получаем настройки прокси для пользователя из базы данных.
    Возвращает кортеж с параметрами для прокси (если прокси существует).
    """
    async for db in get_db():
        result = await db.execute(
            select(ProxySettings).where(ProxySettings.user_id == user_id)
        )
        proxy = result.scalars().first()  # Получаем первое прокси, если оно есть

    if proxy:
        proxy_tuple = (
            proxy.proxy_type,  # Тип прокси, например 'socks5'
            proxy.proxy_host,  # Хост прокси
            proxy.proxy_port,  # Порт прокси
            proxy.proxy_login, # Логин для прокси (если есть)
            proxy.proxy_password  # Пароль для прокси (если есть)
        )
        return proxy_tuple
    else:
        return None  # Если прокси не найдено, возвращаем None

async def get_user_proxies(user_id: int):
    """ Получает список прокси для пользователя """
    async for db in get_db():
        result = await db.execute(select(ProxySettings).where(ProxySettings.user_id == user_id))
        proxies = result.scalars().all()
    return proxies

async def get_session_status(session_file: str, api_id: int, api_hash: str):
    """ Получает статус сессии: авторизована ли она или требует двухфакторной аутентификации """
    session_path = os.path.join(UPLOAD_PATH, session_file)
    client = TelegramClient(session_path, api_id, api_hash)
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            user_display = "🔒 Сессия не авторизована"
        else:
            me = await client.get_me()
            user_display = (
                f"👤 {me.first_name} {me.last_name or ''} (@{me.username})" if me.username else f"ID: {me.id}"
            )
        await client.disconnect()
        return user_display
    except SessionPasswordNeededError:
        return "🔒 Сессия требует двухфакторную аутентификацию"
    except Exception as e:
        return f"⚠ Ошибка: {str(e)}"

# --- Функционал применения/снятия прокси для сессии ---
# (Данный функционал не изменялся)

# --- Функционал для создания сессии и работы с прокси ---

@router.message(F.text == "➕ Создать сессию")
async def request_api_id(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(SessionStates.waiting_for_api_id)
    await message.answer(
        "📌 Получить API ID и API HASH можно тут:\n"
        "🔗 [My Telegram Apps](https://my.telegram.org/apps)\n\n"
        "✏ Введите API ID:",
        parse_mode="Markdown"
    )

@router.message(StateFilter(SessionStates.waiting_for_api_id))
async def get_api_id(message: types.Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer("Введите корректное число для API_ID.")
        return
    await state.update_data(api_id=int(message.text.strip()))
    await state.set_state(SessionStates.waiting_for_api_hash)
    await message.answer("✏️ Введите API HASH:")

@router.message(StateFilter(SessionStates.waiting_for_api_hash))
async def get_api_hash(message: types.Message, state: FSMContext):
    await state.update_data(api_hash=message.text.strip())
    await state.set_state(SessionStates.waiting_for_phone)
    await message.answer("📞 Введите номер телефона:")

@router.message(StateFilter(SessionStates.waiting_for_phone))
async def get_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())

    async for db in get_db():
        result = await db.execute(select(ProxySettings).where(ProxySettings.user_id == message.from_user.id))
        proxies = result.scalars().all()

    if not proxies:
        await state.update_data(proxy="нет")
        await message.answer("У вас нет сохранённых прокси. Будет использовано прямое соединение.")
        # Вызываем хелпер для отправки кода
        await ask_code(message, state)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for proxy in proxies:
        auth = f"{proxy.proxy_login}:{proxy.proxy_password}@" if proxy.proxy_login else ""
        text = f"{proxy.proxy_type}://{auth}{proxy.proxy_host}:{proxy.proxy_port}"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"proxy_select:{proxy.id}"
            )
        ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="Без прокси", callback_data="proxy_select:нет")
    ])
    await message.answer("Выберите прокси:", reply_markup=keyboard)
    await state.set_state(SessionStates.waiting_for_code)

# Хелпер для отправки запроса кода (без декоратора)
async def ask_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data["phone"]
    api_id = data["api_id"]
    api_hash = data["api_hash"]
    proxy_id = data.get("proxy")

    session_path = os.path.join(UPLOAD_PATH, f"{phone.replace('+', '')}.session")
    proxy = None
    if proxy_id and proxy_id != "нет":
        async for db in get_db():
            result = await db.execute(select(ProxySettings).where(ProxySettings.id == int(proxy_id)))
            proxy_obj = result.scalars().first()
        if proxy_obj:
            proxy = (
                'socks5',
                proxy_obj.proxy_host,
                int(proxy_obj.proxy_port),
                True if proxy_obj.proxy_login else False,
                str(proxy_obj.proxy_login or ''),
                str(proxy_obj.proxy_password or '')
            )

    client = TelegramClient(session_path, api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        code_request = await client.send_code_request(phone)
        await state.update_data(
            session=session_path,
            phone_code_hash=code_request.phone_code_hash,
            proxy_input=proxy_id
        )
        # После отправки кода переводим состояние в waiting_for_code
        await state.set_state(SessionStates.waiting_for_code)
        await message.answer("📩 Введите код подтверждения из Telegram:")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке кода: {e}")
        await state.clear()
    finally:
        await client.disconnect()


@router.message(StateFilter(SessionStates.waiting_for_code))
async def verify_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone = data["phone"]
    api_id = data["api_id"]
    api_hash = data["api_hash"]
    code = message.text.strip()
    session_path = data["session"]
    phone_code_hash = data["phone_code_hash"]
    proxy_id = data.get("proxy_input")

    proxy = None
    if proxy_id and proxy_id != "нет":
        async for db in get_db():
            result = await db.execute(
                select(ProxySettings).where(ProxySettings.id == int(proxy_id))
            )
            proxy_obj = result.scalars().first()
        if proxy_obj:
            proxy = (
                'socks5',
                proxy_obj.proxy_host,
                int(proxy_obj.proxy_port),
                True if proxy_obj.proxy_login else False,
                str(proxy_obj.proxy_login or ''),
                str(proxy_obj.proxy_password or '')
            )

    client = TelegramClient(session_path, api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        # Выполняем попытку авторизации
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        # Проверяем, авторизован ли пользователь
        if not await client.is_user_authorized():
            await message.answer("❌ Ошибка: Не удалось авторизоваться. Проверьте правильность кода подтверждения.")
            # Не очищаем состояние, чтобы пользователь мог повторить ввод
            return
        
        # Теперь можно безопасно вызывать методы, требующие соединения
        me = await client.get_me()

        async for db in get_db():
            session_entry = TelegramSession(
                user_id=message.from_user.id,
                session_file=os.path.basename(session_path),
                api_id=api_id,
                api_hash=api_hash,
                proxy_id=int(proxy_id) if proxy_id and proxy_id != "нет" else None
            )
            db.add(session_entry)
            await db.commit()

        await message.answer(f"✅ Сессия успешно создана для @{me.username or me.first_name}")
        await state.clear()

    except SessionPasswordNeededError:
        await state.set_state(SessionStates.waiting_for_password)
        await message.answer("🔐 У аккаунта включена двухфакторная аутентификация. Введите пароль:")
    except PhoneCodeExpiredError:
        await message.answer("❌ Код подтверждения истёк. Попробуйте снова.")
        await state.clear()
    except PhoneCodeInvalidError:
        # Если код неверный, не очищаем состояние — даём возможность повторить ввод
        await message.answer("❌ Код подтверждения неверный. Пожалуйста, введите код ещё раз:")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()
    finally:
        if client.is_connected():
            await client.disconnect()

@router.message(StateFilter(SessionStates.waiting_for_password))
async def enter_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    session_path, api_id, api_hash, password = data["session"], data["api_id"], data["api_hash"], message.text.strip()
    
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        await message.answer(f"✅ Успешный вход для @{me.username or me.first_name}")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await client.disconnect()

async def list_sessions(message: types.Message):
    async for db in get_db():  
        result = await db.execute(
            select(TelegramSession).where(
                TelegramSession.user_id == message.from_user.id
            )
        )
        sessions = result.scalars().all()

    if not sessions:
        await message.answer(escape_md("*У вас пока нет добавленных сессий.*"), parse_mode="MarkdownV2")
        return

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[])
    text = escape_md("*Ваши сессии:*\n\n")

    for session in sessions:
        session_path = f"sessions/{session.session_file}"
        user_display = "🔒 _Сессия не авторизована_"

        try:
            client = TelegramClient(session_path, session.api_id, session.api_hash)
            await client.connect()

            if not await client.is_user_authorized():
                user_display = "🔒 _Сессия не авторизована_"
            else:
                me = await client.get_me()
                first_name = escape_md(me.first_name)
                last_name = escape_md(me.last_name or "").strip()
                username = f"(@{escape_md(me.username)})" if me.username else f"*ID:* `{me.id}`"
                user_display = f"👤 *{first_name}* {last_name} {username}".strip()
            await client.disconnect()
        except SessionPasswordNeededError:
            user_display = "🔒 _Сессия требует двухфакторную аутентификацию_"
        except Exception as e:
            user_display = f"⚠ *Ошибка:* `{escape_md(str(e))}`"

        # Добавляем сессию в текст
        text += f"{user_display}\n"

        buttons = [
            types.InlineKeyboardButton(
                text="Снять прокси" if session.proxy_id else "Одеть прокси",
                callback_data=f"session_{'remove' if session.proxy_id else 'apply'}_proxy:{session.session_file}"
            ),
            types.InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=f"delete_session:{session.session_file}",
            )
        ]
        keyboard.inline_keyboard.append(buttons)
        db.close()

    # Отправка с экранированным текстом
    await message.answer(escape_md(text), reply_markup=keyboard, parse_mode="MarkdownV2")

@router.message(F.text == "📂 Мои сессии")
async def handle_list_sessions(message: types.Message):
    await list_sessions(message)
    

@router.callback_query(lambda c: c.data.startswith("delete_session:"))
async def delete_session(callback: types.CallbackQuery):
    session_file = callback.data.split(":")[1]
    async for db in get_db():
        result = await db.execute(select(TelegramSession).where(
            TelegramSession.user_id == callback.from_user.id,
            TelegramSession.session_file == session_file
        ))
        session = result.scalars().first()
        if not session:
            await callback.answer("❌ Сессия не найдена.", show_alert=True)
            return
        await db.delete(session)
        await db.commit()
        path = f"sessions/{session_file}"
        if os.path.exists(path):
            os.remove(path)
    await callback.message.answer(f"✅ Сессия {session_file} удалена.")
    await callback.answer()
    await db.close()


    await callback.answer()
@router.message(StateFilter(SessionStates.waiting_for_password))
async def verify_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    session_str = data["session"]
    api_id = data["api_id"]
    api_hash = data["api_hash"]
    phone = data["phone"]
    proxy_id = data.get("proxy_input")
    password = message.text.strip()

    proxy = None
    if proxy_id and proxy_id != "нет":
        async for db in get_db():
            result = await db.execute(select(ProxySettings).where(ProxySettings.id == int(proxy_id)))
            proxy_obj = result.scalars().first()
        if proxy_obj:
            proxy = (
                'socks5',
                proxy_obj.proxy_host,
                int(proxy_obj.proxy_port),
                True if proxy_obj.proxy_login else False,
                str(proxy_obj.proxy_login or ''),
                str(proxy_obj.proxy_password or '')
            )

    client = TelegramClient(StringSession(session_str), api_id, api_hash, proxy=proxy)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()

        session_filename = f"{phone.replace('+', '')}.session"
        full_path = os.path.join(UPLOAD_PATH, session_filename)
        with open(full_path, "wb") as f:
            f.write(client.session.save().encode())

        async for db in get_db():
            db.add(TelegramSession(
                user_id=message.from_user.id,
                phone=phone,
                session_file=session_filename,
                api_id=api_id,
                api_hash=api_hash,
                proxy_id=int(proxy_id) if proxy_id and proxy_id != "нет" else None
            ))
            await db.commit()

        await message.answer("✅ Авторизация с двухфакторной аутентификацией завершена.")
    except Exception as e:
        await message.answer(f"❌ Ошибка авторизации: {e}")
    finally:
        await disconnect()
        await state.clear()



# --- Функционал применения/снятия прокси для новых сессий через FSM ---
@router.callback_query(F.data.startswith("proxy_select:"))
async def select_proxy(callback: types.CallbackQuery, state: FSMContext):
    proxy_id = callback.data.split(":")[1]
    await state.update_data(proxy=proxy_id)
    await callback.message.edit_text("Прокси выбран. Отправляем код авторизации...")
    await ask_code(callback.message, state)
    await callback.answer()

async def connect_session(session: TelegramSession, db):
    """Подключение к Telegram с учётом прокси (асинхронно, корректно)"""
    proxy = None

    if session.proxy_id:
        proxy_data = await db.execute(
            select(ProxySettings).where(ProxySettings.id == session.proxy_id)
        )
        proxy_obj = proxy_data.scalars().first()

        if proxy_obj and proxy_obj.proxy_type.lower() == "socks5":
            proxy = (
                'socks5',
                proxy_obj.proxy_host,
                int(proxy_obj.proxy_port),
                True if proxy_obj.proxy_login else False,
                str(proxy_obj.proxy_login or ''),
                str(proxy_obj.proxy_password or '')
            )
        else:
            print("❌ Только SOCKS5 поддерживается.")
            return None

    print(f"[DEBUG] Подключение через прокси: {proxy}")

    try:
        client = TelegramClient(session.session_file, session.api_id, session.api_hash, proxy=proxy)
        await client.connect()
        if not await client.is_user_authorized():
            print("❌ Сессия не авторизована.")
            await client.disconnect()
            return None
        me = await client.get_me()
        print(f"✅ Сессия подключена: {me.username or me.first_name}")
        return client
    except Exception as e:
        print(f"❌ Ошибка подключения: {type(e).__name__}: {e}")
        return None


    try:
        client = TelegramClient(session.session_file, session.api_id, session.api_hash, proxy=proxy)
        await client.connect()
        if not await client.is_user_authorized():
            print("❌ Сессия не авторизована.")
            await client.disconnect()
            return None
        me = await client.get_me()
        print(f"✅ Сессия подключена: {me.username or me.first_name}")
        return client
    except Exception as e:
        print(f"❌ Ошибка подключения: {type(e).__name__}: {e}")
        return None


@router.callback_query(lambda c: c.data.startswith("apply_proxy_to_session:"))
async def apply_proxy_to_session(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора прокси для сессии.
    Формат callback_data: apply_proxy_to_session:<session_file>:<proxy_id>
    """
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Неверный формат данных.")
        return
    session_file, proxy_id = parts[1], int(parts[2])
    
    async for db in get_db():
        result = await db.execute(
            select(TelegramSession).where(
                TelegramSession.user_id == callback.from_user.id,
                TelegramSession.session_file == session_file
            )
        )
        session = result.scalars().first()
        if not session:
            await callback.answer("❌ Сессия не найдена.", show_alert=True)
            return

        result = await db.execute(
            select(ProxySettings).where(ProxySettings.id == proxy_id)
        )
        proxy_obj = result.scalars().first()
        if not proxy_obj:
            await callback.answer("❌ Прокси не найден.", show_alert=True)
            return

        proxy = (
            'socks5',
            proxy_obj.proxy_host,
            int(proxy_obj.proxy_port),
            True if proxy_obj.proxy_login else False,
            str(proxy_obj.proxy_login or ''),
            str(proxy_obj.proxy_password or '')
        )
        
        if not await test_proxy(proxy):
            await callback.message.edit_text("❌ Прокси не работает. Выберите другой.")
            await callback.answer()
            return

        session.proxy_id = proxy_id
        await db.commit()
        
        client = await connect_session(session, db)
        if client:
            await callback.message.edit_text(f"✅ Прокси применён и сессия {session_file} подключена.")
        else:
            await callback.message.edit_text(f"❌ Ошибка подключения сессии {session_file}.")
    
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("session_remove_proxy:"))
async def session_remove_proxy(callback: types.CallbackQuery, state: FSMContext):
    """
    Снятие прокси с сессии.
    Формат callback_data: session_remove_proxy:<session_file>
    """
    session_file = callback.data.split(":")[1]
    async for db in get_db():
        result = await db.execute(
            select(TelegramSession).where(
                TelegramSession.user_id == callback.from_user.id,
                TelegramSession.session_file == session_file
            )
        )
        session = result.scalars().first()
        if not session:
            await callback.answer("❌ Сессия не найдена.", show_alert=True)
            return
        session.proxy_id = None
        await db.commit()
    await callback.message.edit_text(f"✅ Прокси снят с сессии {session_file}.")
    await callback.answer()

# --- Функционал применения/снятия прокси для новых сессий через FSM ---

@router.message(F.text == "🔌 Применить прокси")
async def choose_proxy_for_new_session(message: types.Message, state: FSMContext):
    # Применяется для новых сессий через FSM – аналог функции для создания сессии
    async for db in get_db():
        result = await db.execute(select(ProxySettings).where(ProxySettings.user_id == message.from_user.id))
        proxies = result.scalars().all()
    if not proxies:
        await message.answer("❌ У вас нет сохранённых прокси. Добавьте прокси через соответствующий раздел.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for proxy in proxies:
        auth = f"{proxy.proxy_login}:{proxy.proxy_password}@" if proxy.proxy_login else ""
        button_text = f"{proxy.proxy_type}://{auth}{proxy.proxy_host}:{proxy.proxy_port}"
        keyboard.inline_keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"select_proxy:{proxy.id}")])
    await message.answer("Выберите прокси для применения к новым сессиям:", reply_markup=keyboard)

@router.callback_query(lambda c: c.data.startswith("select_proxy:"))
async def select_proxy_callback(callback: types.CallbackQuery, state: FSMContext):
    proxy_id = int(callback.data.split(":")[1])
    await state.update_data(proxy_id=proxy_id)
    await callback.message.edit_text("✅ Прокси выбран для текущих сессий. При создании сессии будет использоваться данный прокси.")
    await callback.answer()

@router.message(F.text == "❌ Снять прокси")
async def remove_proxy_for_new_session(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if "proxy_id" in data:
        await state.update_data(proxy_id=None)
        await message.answer("✅ Прокси снят. Новые сессии будут создаваться без прокси.", reply_markup=session_keyboard)
    else:
        await message.answer("ℹ Прокси не применён для новых сессий.", reply_markup=session_keyboard)