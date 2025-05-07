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