from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import random
import math
# Не забудьте импортировать необходимые модели, например:
# from db.models import TelegramSession, Flow

async def generate_flows_for_user(db: AsyncSession, user_id: int):
    """Автоматически создает потоки по 3 сессии для пользователя.
       Сессии распределяются случайно, но внутри каждого потока их порядок будет 1, 2, 3.
    """
    # Получаем все сессии пользователя
    result = await db.execute(select(TelegramSession).where(TelegramSession.user_id == user_id))
    user_sessions = result.scalars().all()

    if len(user_sessions) < 3:
        raise ValueError("Недостаточно сессий для создания потоков (минимум 3).")

    # Определяем количество потоков (по 3 сессии на поток)
    num_flows = math.floor(len(user_sessions) / 3)

    # Перемешиваем сессии для случайного распределения
    random.shuffle(user_sessions)

    created_flows = []  # Список созданных потоков

    # Формируем потоки методом round-robin:
    # для каждого потока берем сессии с индексами: i, i+num_flows, i+2*num_flows
    for i in range(num_flows):
        sessions_subset = []
        for j in range(3):
            index = i + j * num_flows
            if index < len(user_sessions):
                sessions_subset.append(user_sessions[index])
        # Создаем уникальное имя потока с последовательным номером
        flow_name = f"Flow_{user_id}_{i+1}"

        # Проверяем, существует ли уже поток с таким именем
        result = await db.execute(
            select(Flow).where(Flow.name == flow_name, Flow.user_id == user_id)
        )
        existing_flow = result.scalars().first()
        if existing_flow:
            continue  # Пропускаем, если поток уже существует

        # Создаем новый поток и прикрепляем выбранные сессии
        new_flow = Flow(name=flow_name, user_id=user_id)
        new_flow.sessions.extend(sessions_subset)
        db.add(new_flow)
        created_flows.append(new_flow)

    # Сохраняем изменения в базе
    await db.commit()
    return created_flows
