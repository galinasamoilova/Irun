# Пошаговая инструкция: Хостинг бота на Bothost.ru через GitHub

Хостинг **[Bothost.ru](https://bothost.ru/)** позволяет автоматически запускать Telegram-бота прямо из вашего репозитория на GitHub. При каждом обновлении кода на GitHub бот может автоматически перезапускаться с новыми изменениями.

---

## ⚠️ ВАЖНОЕ ПРАВИЛО БЕЗОПАСНОСТИ GITHUB!
**Никогда не загружайте на GitHub файлы `.env` и `credentials.json`!**
* На GitHub работают автоматические сканеры безопасности: если секретный ключ Google или токен Telegram попадут в репозиторий, Telegram и Google мгновенно заблокируют их в целях безопасности.
* Мы создали специальный файл [`.gitignore`](file:///c:/Users/GSamoylova/Documents/Gemini/telegram_tag_bot/.gitignore), который автоматически скрывает эти два файла от Git.
* Сами секреты передаются боту через панель управления **Bothost.ru** (Раздел «Переменные окружения»).

---

## 📂 Какие файлы должны быть загружены на GitHub:
1. `bot.py` — основной код бота.
2. `requirements.txt` — список библиотек для автоустановки.
3. `Dockerfile` — инструкция сборки контейнера (на случай развертывания через Docker).
4. `docker-compose.yml`
5. `.dockerignore`
6. `.gitignore` — защита паролей и ключей.
7. `.env.example` — безопасный шаблон настроек.
8. `credentials.json.example` — безопасный шаблон ключа Google.
9. `README.md`

---

## 🚀 Пошаговый процесс:

### Шаг 1. Создание репозитория на GitHub
1. Зайдите на свой аккаунт на [github.com](https://github.com/).
2. В правом верхнем углу нажмите **«+»** → **«New repository»**.
3. Укажите название (например, `running-telegram-bot`).
4. Выберите тип: **Private** (приватный репозиторий доступен только вам).
5. Нажмите зеленую кнопку **«Create repository»**.

---

### Шаг 2. Загрузка файлов на GitHub

#### Вариант А: Быстро через браузер (без командной строки Git)
1. На странице созданного репозитория нажмите на ссылку **«uploading an existing file»** (или перетащите файлы мышкой).
2. Выделите и перетащите следующие файлы из папки `telegram_tag_bot`:
   - `bot.py`
   - `requirements.txt`
   - `Dockerfile`
   - `docker-compose.yml`
   - `.dockerignore`
   - `.gitignore`
   - `.env.example`
   - `credentials.json.example`
   - `README.md`
   *(⚠️ Убедитесь, что файлов `.env` и `credentials.json` нет среди загружаемых!)*
3. Нажмите зеленую кнопку **«Commit changes»**.

#### Вариант Б: Через терминал Git
Если на компьютере установлен Git:
```bash
cd C:\Users\GSamoylova\Documents\Gemini\telegram_tag_bot
git init
git add bot.py requirements.txt Dockerfile docker-compose.yml .dockerignore .gitignore .env.example credentials.json.example README.md
git commit -m "Initial commit of running bot"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/running-telegram-bot.git
git push -u origin main
```

---

### Шаг 3. Регистрация и привязка GitHub на Bothost.ru
1. Перейдите на сайт **[bothost.ru](https://bothost.ru/)** и зарегистрируйтесь (можно войти через Telegram).
2. Перейдите в раздел **«Боты»** (или «Серверы») → нажмите **«Создать бота»**.
3. В качестве источника выберите **«GitHub»**.
4. Нажмите **«Подключить GitHub»** и разрешите доступ Bothost к вашему репозиторию `running-telegram-bot`.
5. Выберите тариф: минимального тарифа (Python 3.12, 512 МБ – 1 ГБ RAM) хватит с большим запасом.

---

### Шаг 4. Настройка секретов (Переменных окружения) на Bothost.ru
Так как файл `.env` мы не загружали на GitHub, задаем эти настройки во вкладке **«Переменные окружения»** (Environment Variables) в панели Bothost:

Добавьте переменные (в точности как в нашем `.env`):
* `BOT_TOKEN` = `8801667790:AAGTuwXMY3rXNfGm0APSjNFvsUTlly5NcRw`
* `SPREADSHEET_URL` = `https://docs.google.com/spreadsheets/d/1g_TdzOEkOmEYfHji-bhfB3WCcyXEQeJbHERtyZCKnIM/edit`
* `PRIMARY_TAG` = `#япобегал`
* `ALLOWED_CHAT_ID` = `-1003705849792`
* `ALLOWED_THREAD_ID` = `3`

---

### Шаг 5. Передача ключа Google (credentials.json)
Для передачи ключа Google есть два очень простых способа:

* **Способ 1 (Самый простой через файл):**  
  После создания бота на Bothost откройте вкладку **«Файлы»** (Файловый менеджер) и нажмите **«Загрузить файл»** (Upload). Загрузите туда ваш оригинальный файл `credentials.json`. Этот файл останется только на сервере Bothost и не попадет на GitHub.
  
* **Способ 2 (Через переменную окружения):**  
  Откройте файл `credentials.json` в блокноте, скопируйте весь его текст целиком и во вкладке «Переменные окружения» на Bothost добавьте переменную:  
  `GOOGLE_CREDENTIALS_JSON` = *(вставьте скопированный текст ключа)*.  
  Наш бот автоматически умеет считывать ключ прямо из этой переменной!

---

### Шаг 6. Запуск и проверка
1. На Bothost нажмите кнопку **«Запустить бота»**.
2. Перейдите во вкладку **«Логи»** (Консоль).
3. Должны появиться строчки успешного старта:
   ```text
   [INFO] - Подключились к таблице: 'Копия общей таблицы бега'
   [INFO] - Динамически загружен справочник 'Участники чата'...
   [INFO] - Беговой бот запущен и слушает чат! Теги: #япобегал и #??км
   [INFO] - Ограничение по теме форума: ALLOWED_THREAD_ID=3
   ```
4. Отправьте тестовое сообщение в топик «Пробежки»: `#япобегал #5км` — бот поставит реакцию **👍**, а километры занесутся в Google Таблицу.
