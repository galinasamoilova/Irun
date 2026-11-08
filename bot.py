import os
import re
import json
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReactionTypeEmoji
import gspread

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL")
CREDENTIALS_FILE = os.getenv("CREDENTIALS_FILE", "credentials.json")
PRIMARY_TAG = os.getenv("PRIMARY_TAG", "#япобегал").strip().lower()

# Ограничения по чату и теме (если нужно слушать только одну группу или конкретную тему)
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
ALLOWED_THREAD_ID = os.getenv("ALLOWED_THREAD_ID", "").strip()

# Файл для сохранения очереди отложенных пробежек
PENDING_RUNS_FILE = "pending_runs.json"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)
logger = logging.getLogger(__name__)

# Блокировки
sheet_lock = asyncio.Lock()
queue_lock = asyncio.Lock()

# Названия месяцев на русском языке для вкладок
MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

# Регулярные выражения для распознавания хэштегов
TAG_RUN_REGEX = re.compile(rf"{re.escape(PRIMARY_TAG)}\b", re.IGNORECASE)
TAG_KM_REGEX = re.compile(r"#(\d+(?:[.,]\d+)?)\s*км\b", re.IGNORECASE)

# Клиент и кеш Google Sheets
gc_client = None
spreadsheet_instance = None
users_mapping_cache = None
users_mapping_time = 0
CACHE_TTL = 300  # Кеширование соответствия пользователей на 5 минут


# ==========================================
# ОЧЕРЕДЬ ОТЛОЖЕННЫХ ПРОБЕЖЕК (PENDING QUEUE)
# ==========================================

def load_pending_runs_sync() -> list[dict]:
    """Считывает сохраненную очередь отложенных пробежек с диска."""
    path = Path(PENDING_RUNS_FILE)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при чтении {PENDING_RUNS_FILE}: {e}")
        return []


def save_pending_runs_sync(runs: list[dict]) -> None:
    """Сохраняет очередь отложенных пробежек на диск."""
    try:
        with open(PENDING_RUNS_FILE, "w", encoding="utf-8") as f:
            json.dump(runs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка при сохранении {PENDING_RUNS_FILE}: {e}")


async def add_pending_run(run_data: dict) -> None:
    """Добавляет отчет в очередь ожидания вкладки."""
    async with queue_lock:
        runs = await asyncio.to_thread(load_pending_runs_sync)
        # Проверяем на дубликаты по chat_id и message_id
        for r in runs:
            if r.get("chat_id") == run_data.get("chat_id") and r.get("message_id") == run_data.get("message_id"):
                return
        runs.append(run_data)
        await asyncio.to_thread(save_pending_runs_sync, runs)
        u_name = run_data.get("user", {}).get("username") or run_data.get("user", {}).get("full_name")
        logger.info(
            f"Отчет от @{u_name} ({run_data.get('km')} км) помещен в очередь ожидания вкладки месяца. "
            f"Всего в очереди: {len(runs)}"
        )


async def set_acknowledgement_reaction(bot: Bot, chat_id: int, message_id: int) -> bool:
    """
    Надежно устанавливает реакцию подтверждения на сообщение в Telegram.
    Сначала пробует 👍 (гарантированно доступна во всех группах), затем 🔥, ❤️, 🏃, 👏.
    """
    for emoji in ["👍", "🔥", "❤️", "🏃", "👏"]:
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
            logger.info(f"Успешно установлена реакция {emoji} на сообщение {message_id}")
            return True
        except Exception as e:
            logger.debug(f"Не удалось установить реакцию {emoji}: {e}")
            continue

    logger.warning(f"Не удалось установить ни одну реакцию на сообщение {message_id}. Возможно, реакции отключены в настройках группы.")
    return False


async def process_pending_runs(bot: Bot) -> None:
    """Периодически проверяет появление вкладок и записывает отложенные пробежки."""
    async with queue_lock:
        runs = await asyncio.to_thread(load_pending_runs_sync)
        if not runs:
            return

        remaining_runs = []
        for r in runs:
            # Воссоздаем объект пользователя
            user_data = r.get("user") or {}
            class TempUser:
                username = user_data.get("username")
                full_name = user_data.get("full_name", "")
                first_name = user_data.get("first_name", "")
                id = user_data.get("id")

            msg_dt = datetime.fromisoformat(r["dt_iso"])
            km = r["km"]

            success, info, total_day_km = await record_run(TempUser(), msg_dt, km)
            if success:
                logger.info(
                    f"Отложенная пробежка от @{TempUser.username} успешно записана в таблицу! "
                    f"Ставим реакцию на сообщение {r['message_id']}..."
                )
                await set_acknowledgement_reaction(bot, r["chat_id"], r["message_id"])
            else:
                # Если вкладка все еще не создана — оставляем в очереди
                remaining_runs.append(r)

        if len(remaining_runs) != len(runs):
            await asyncio.to_thread(save_pending_runs_sync, remaining_runs)
            logger.info(f"Очередь отложенных пробежек обновлена. Осталось в ожидании: {len(remaining_runs)}")


async def pending_runs_worker(bot: Bot) -> None:
    """Фоновая задача: проверяет очередь каждые 20 секунд."""
    while True:
        try:
            await asyncio.sleep(20)
            await process_pending_runs(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в фоновом обработчике очереди: {e}", exc_info=True)


# ==========================================
# РАБОТА С GOOGLE ТАБЛИЦАМИ (GOOGLE SHEETS)
# ==========================================

def get_spreadsheet(force_refresh: bool = False) -> gspread.Spreadsheet:
    """Возвращает объект Google Таблицы с авторизацией через сервисный аккаунт."""
    global gc_client, spreadsheet_instance
    if spreadsheet_instance is None or force_refresh:
        google_creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
        if gc_client is None:
            if google_creds_json:
                try:
                    creds_dict = json.loads(google_creds_json)
                    gc_client = gspread.service_account_from_dict(creds_dict)
                    logger.info("Успешная авторизация в Google через переменную GOOGLE_CREDENTIALS_JSON.")
                except Exception as e:
                    logger.error(f"Ошибка парсинга переменной GOOGLE_CREDENTIALS_JSON: {e}")

            if gc_client is None:
                if not Path(CREDENTIALS_FILE).exists():
                    raise FileNotFoundError(
                        f"Ключ Google не найден: файл '{CREDENTIALS_FILE}' отсутствует "
                        f"и переменная GOOGLE_CREDENTIALS_JSON не задана!"
                    )
                gc_client = gspread.service_account(filename=CREDENTIALS_FILE)

        spreadsheet_instance = gc_client.open_by_url(SPREADSHEET_URL)
        logger.info(f"Подключились к таблице: '{spreadsheet_instance.title}' (обновлено: {force_refresh})")
    return spreadsheet_instance


def get_monthly_worksheet(sh: gspread.Spreadsheet, sheet_title: str) -> gspread.Worksheet | None:
    """
    Ищет вкладку месяца без учета регистра и лишних пробелов.
    Если вкладка была создана недавно во время работы бота — принудительно обновляет список листов.
    """
    target = sheet_title.strip().lower()

    # 1. Сначала ищем среди уже загруженных вкладок
    try:
        for w in sh.worksheets():
            if w.title.strip().lower() == target:
                return w
    except Exception as e:
        logger.debug(f"Ошибка при первичном обходе листов: {e}")

    # 2. Если не найдена — принудительно переоткрываем таблицу, чтобы получить свежий список вкладок
    try:
        fresh_sh = get_spreadsheet(force_refresh=True)
        for w in fresh_sh.worksheets():
            if w.title.strip().lower() == target:
                return w
    except Exception as e:
        logger.warning(f"Ошибка при повторном поиске вкладки '{sheet_title}': {e}")

    return None


def get_users_mapping(sh: gspread.Spreadsheet, force_reload: bool = False) -> dict:
    """
    Динамически сканирует вкладку 'Участники чата' БЕЗ привязки к конкретным столбцам.
    Находит все ячейки с @username и связывает их со всеми именами/никами в этой же строке.
    """
    global users_mapping_cache, users_mapping_time
    now = time.time()
    if not force_reload and users_mapping_cache is not None and (now - users_mapping_time) < CACHE_TTL:
        return users_mapping_cache

    mapping = {}
    try:
        ws_users = sh.worksheet("Участники чата")
        data = ws_users.get_all_values()
        
        # Сканируем каждую строку независимо от расположения колонок
        for row in data[1:]:
            # Находим любые ячейки, содержащие никнейм через @
            handles = [c.strip().lstrip("@").lower() for c in row if "@" in c and len(c.strip()) > 1]
            # Собираем остальные текстовые значения строки (имена, фамилии, никнеймы), исключая чистые числа
            aliases = {
                c.strip().lower() for c in row 
                if c.strip() and "@" not in c and not c.strip().isdigit() and len(c.strip()) > 1
            }

            for h in handles:
                if h not in mapping:
                    mapping[h] = set()
                mapping[h].update(aliases)
                mapping[h].add(h)

        users_mapping_cache = mapping
        users_mapping_time = now
        logger.info(f"Динамически загружен справочник 'Участники чата': {len(mapping)} профилей.")
    except Exception as e:
        logger.warning(f"Не удалось загрузить справочник 'Участники чата': {e}")
        users_mapping_cache = {}

    return users_mapping_cache


def get_day_column(header_rows: list[list[str]], day: int) -> int | None:
    """
    Динамически находит номер столбца для нужного дня месяца.
    Ищет число дня (например, '8') в строках заголовка таблицы.
    """
    day_str = str(day)
    for r in header_rows:
        for idx, cell in enumerate(r):
            if cell.strip() == day_str:
                return idx + 1
    return None


def find_participant_row(all_rows: list[list[str]], user, users_map: dict, day_col_indices: set[int]) -> tuple[int | None, str | None]:
    """
    Динамически ищет строку участника в таблице месяца:
    - Проверяет все текстовые ячейки в строке (не привязано к столбцу B).
    - Сопоставляет по @username, имени в Telegram и любым псевдонимам из 'Участники чата'.
    """
    tg_username = user.username.lower().lstrip("@") if user.username else ""
    full_name = user.full_name.strip().lower() if user.full_name else ""
    first_name = user.first_name.strip().lower() if user.first_name else ""

    # Набор целевых совпадений для данного пользователя
    targets = set()
    if tg_username:
        targets.add(tg_username)
        targets.add(f"@{tg_username}")
        # Добавляем все связанные псевдонимы из 'Участники чата'
        if tg_username in users_map:
            targets.update(users_map[tg_username])

    if full_name and len(full_name) > 1:
        targets.add(full_name)
    if first_name and len(first_name) > 2:
        targets.add(first_name)

    # Исключаем слишком короткие или пустые совпадения
    targets = {t for t in targets if t and len(t) > 1}

    # Ищем со строки 3 (индекс 2), пропуская заголовки
    for r_idx in range(2, len(all_rows)):
        row = all_rows[r_idx]
        for c_idx, cell in enumerate(row):
            # Пропускаем столбцы с днями месяца, чтобы даты не спутались с именами
            if c_idx in day_col_indices:
                continue

            val = cell.strip().lower()
            if not val or val in ["#ref!", "итого", "рейтинг"]:
                continue

            if val in targets:
                display_name = cell.strip()
                return r_idx + 1, display_name

    return None, None


def sync_record_run(user, msg_dt: datetime, km: float) -> tuple[bool, str, float]:
    """Синхронная динамическая запись километров в таблицу."""
    sh = get_spreadsheet()
    year = msg_dt.year
    month = msg_dt.month
    day = msg_dt.day

    sheet_title = f"{MONTHS_RU[month]} {str(year)[-2:]}"

    # 1. Проверяем наличие вкладки текущего месяца (с автообновлением списка вкладок)
    ws = get_monthly_worksheet(sh, sheet_title)
    if ws is None:
        msg = f"Вкладка '{sheet_title}' пока не создана в таблице. Ожидаем создания администратором."
        logger.warning(msg)
        return False, msg, 0.0

    # Считываем строки таблицы для динамического поиска
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        msg = f"Вкладка '{sheet_title}' пуста или повреждена."
        logger.error(msg)
        return False, msg, 0.0

    # 2. Динамически определяем столбец для текущего дня месяца
    header_rows = all_rows[:2]
    col_idx = get_day_column(header_rows, day)
    if col_idx is None:
        msg = f"Не удалось динамически найти столбец для дня {day} во вкладке '{sheet_title}'."
        logger.error(msg)
        return False, msg, 0.0

    # Индексы всех столбцов с датами для исключения из поиска имен
    day_col_indices = {
        idx for r in header_rows 
        for idx, cell in enumerate(r) 
        if cell.strip().isdigit() and 1 <= int(cell.strip()) <= 31
    }

    # 3. Динамически ищем строку участника
    users_map = get_users_mapping(sh)
    row_idx, matched_name = find_participant_row(all_rows, user, users_map, day_col_indices)

    # Если не нашли — принудительно обновляем кеш 'Участники чата' и ищем снова
    if row_idx is None:
        users_map = get_users_mapping(sh, force_reload=True)
        row_idx, matched_name = find_participant_row(all_rows, user, users_map, day_col_indices)

    user_label = f"@{user.username}" if user.username else user.full_name
    if row_idx is None:
        msg = f"Участник {user_label} не найден во вкладке '{sheet_title}' (проверены все столбцы). Пробежка не внесена."
        logger.warning(msg)
        return False, msg, 0.0

    # 4. Читаем текущее значение в ячейке дня (суммируем, если уже была пробежка)
    current_val_str = ws.cell(row_idx, col_idx).value
    current_km = 0.0
    if current_val_str:
        try:
            current_km = float(str(current_val_str).replace(",", "."))
        except ValueError:
            current_km = 0.0

    total_day_km = round(current_km + km, 2)
    final_val = int(total_day_km) if total_day_km.is_integer() else total_day_km

    # 5. Записываем только в найденную ячейку
    ws.update_cell(row_idx, col_idx, final_val)
    logger.info(
        f"Успешно: {user_label} ({matched_name}) записано {km} км (всего за {day}-е число: {total_day_km} км) "
        f"во вкладку '{sheet_title}' [строка {row_idx}, столбец {col_idx}]."
    )
    return True, sheet_title, total_day_km


async def record_run(user, msg_dt: datetime, km: float) -> tuple[bool, str, float]:
    """Асинхронная обертка с блокировкой для предотвращения гонок запросов."""
    async with sheet_lock:
        return await asyncio.to_thread(sync_record_run, user, msg_dt, km)


# ==========================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ TELEGRAM
# ==========================================

dp = Dispatcher()


@dp.message(Command("topic_info", "id"))
async def handle_topic_info(message: Message) -> None:
    """Команда для быстрого определения ID чата и ID темы (ветки форума)."""
    chat_id = message.chat.id
    thread_id = message.message_thread_id
    chat_title = message.chat.title or "Личные сообщения"
    
    response_text = (
        f"ℹ️ **Информация о чате и теме:**\n\n"
        f"📌 **Название чата:** {chat_title}\n"
        f"🆔 **ID чата (ALLOWED_CHAT_ID):** `{chat_id}`\n"
        f"🧵 **ID темы / ветки (ALLOWED_THREAD_ID):** `{thread_id or 'Основной чат (без темы)'}`\n\n"
        f"Скопируйте эти значения в файл `.env`, если хотите ограничить бота только этой темой."
    )
    await message.reply(response_text, parse_mode="Markdown")


@dp.message(F.text | F.caption)
async def handle_running_report(message: Message, bot: Bot) -> None:
    """Обработчик сообщений с отчетами о пробежках."""
    # 1. Фильтр по ID чата (если задан в .env)
    if ALLOWED_CHAT_ID and str(message.chat.id) != ALLOWED_CHAT_ID:
        return

    # 2. Фильтр по ID темы форума (если задан в .env)
    if ALLOWED_THREAD_ID:
        msg_thread = str(message.message_thread_id or "")
        if msg_thread != ALLOWED_THREAD_ID:
            return

    text = (message.text or message.caption or "").strip()

    # Проверка основного тега #япобегал
    if not TAG_RUN_REGEX.search(text):
        return

    # Проверка тега дистанции #??км (#5км, #10.5км, #21,1км)
    km_match = TAG_KM_REGEX.search(text)
    if not km_match:
        logger.info(f"Сообщение содержит {PRIMARY_TAG}, но не содержит хэштег дистанции (#??км): '{text[:50]}...'")
        return

    # Извлечение дистанции
    km_str = km_match.group(1).replace(",", ".")
    try:
        km = float(km_str)
        if km <= 0:
            return
    except ValueError:
        return

    user = message.from_user
    if not user:
        return

    # Дата сообщения по московскому времени (UTC+3)
    msg_dt = message.date.astimezone(timezone(timedelta(hours=3)))

    user_label = f"@{user.username}" if user.username else user.full_name
    thread_info = f" [тема {message.message_thread_id}]" if message.message_thread_id else ""
    logger.info(f"Получен отчет от {user_label}{thread_info}: {km} км ({msg_dt.strftime('%d.%m.%Y %H:%M')})")

    try:
        success, info_or_error, total_day_km = await record_run(user, msg_dt, km)
        if success:
            # Ставим реакцию подтверждения на сообщение в чате
            await set_acknowledgement_reaction(bot, message.chat.id, message.message_id)
        else:
            # Если вкладка месяца еще не создана — помещаем отчет в очередь ожидания!
            if "пока не создана в таблице" in info_or_error:
                run_data = {
                    "chat_id": message.chat.id,
                    "message_id": message.message_id,
                    "user": {
                        "username": user.username,
                        "full_name": user.full_name,
                        "first_name": user.first_name,
                        "id": user.id
                    },
                    "dt_iso": msg_dt.isoformat(),
                    "km": km
                }
                await add_pending_run(run_data)
            else:
                logger.warning(f"Пробежка не внесена: {info_or_error}")

    except Exception as e:
        logger.error(f"Непредвиденная ошибка при обработке сообщения: {e}", exc_info=True)


async def main() -> None:
    if not BOT_TOKEN or "ВАШ_ТОКЕН" in BOT_TOKEN:
        logger.error("Не указан токен бота BOT_TOKEN в файле .env!")
        return
    if not SPREADSHEET_URL:
        logger.error("Не указана ссылка на таблицу SPREADSHEET_URL в файле .env!")
        return

    # Проверка соединения с таблицей при запуске
    try:
        sh = get_spreadsheet()
        logger.info(f"Соединение с Google Таблицей '{sh.title}' успешно подтверждено.")
        get_users_mapping(sh, force_reload=True)
    except Exception as e:
        logger.error(f"Не удалось подключиться к Google Таблице: {e}", exc_info=True)
        return

    bot = Bot(token=BOT_TOKEN)
    logger.info(f"Беговой бот запущен и слушает чат! Теги: {PRIMARY_TAG} и #??км")
    if ALLOWED_CHAT_ID:
        logger.info(f"Ограничение по чату: ALLOWED_CHAT_ID={ALLOWED_CHAT_ID}")
    if ALLOWED_THREAD_ID:
        logger.info(f"Ограничение по теме форума: ALLOWED_THREAD_ID={ALLOWED_THREAD_ID}")

    # Запуск фонового обработчика отложенной очереди (Pending Queue)
    worker_task = asyncio.create_task(pending_runs_worker(bot))
    
    # Сразу пробуем обработать накопившуюся очередь при старте
    asyncio.create_task(process_pending_runs(bot))

    try:
        await dp.start_polling(bot, allowed_updates=["message"])
    finally:
        worker_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
