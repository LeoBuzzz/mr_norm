# MR Norm Bot

Отдельный проект для ботов VK и Telegram. Боты используют коллекцию Qdrant
`mr_norm_docs_bge_m3_rtf_v2_20261011`. Для установки на другой компьютер нужны
Windows, Python 3.12, Docker Desktop и доступ к приватному репозиторию GitHub.

Код хранится в GitHub. База Qdrant, локальные чанки и ключи передаются отдельно:
они исключены из Git.

## Исходная машина: подготовить данные

1. Откройте PowerShell в исходном проекте `mr_norm`. Убедитесь, что Qdrant запущен.
2. Экспортируйте действующую коллекцию и связанные файлы в **пустую** папку:

   ```powershell
   cd C:\PY\PythonProject\mr_norm
   .\venv312\Scripts\python.exe scripts\bot_db_transfer.py export --directory C:\mr_norm_bot_transfer
   ```

   Скрипт выгружает точки и векторы Qdrant, чанки v2, каталог документов и
   дополнительную таблицу соответствий, если она есть. Повторный экспорт нужно
   делать в другую пустую папку. Для первоначального переноса пакет уже
   подготовлен в `C:\PY\PythonProject\mr_norm_bot\transfer`.

3. Скопируйте папку экспорта на целевую машину под именем `transfer` в корень
   `mr_norm_bot`. Отдельно и безопасным способом скопируйте файлы `keys`,
   `VK_token.txt` и `bot_token.txt` из корня исходного проекта.
4. Перед запуском ботов на целевой машине остановите их на исходной: одни и те
   же токены не должны использоваться двумя экземплярами одновременно.

## Целевая машина: установить и запустить

1. Получите код и перейдите в папку проекта:

   ```powershell
   git clone https://github.com/LeoBuzzz/mr_norm_bot.git
   cd mr_norm_bot
   ```

2. Положите папку `transfer` и три файла ключей в корень `mr_norm_bot`.
   Проверьте, что существуют `transfer\collection.json` и
   `transfer\points.jsonl`. GitHub эти данные не содержит.
3. Установите окружение и запустите Qdrant:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_bot.ps1
   docker compose up -d qdrant
   ```

4. Импортируйте базу и локальные данные:

   ```powershell
   .\venv312\Scripts\python.exe scripts\bot_db_transfer.py import --directory transfer
   ```

   Скрипт проверит количество точек после импорта и восстановит каталог из
   перенесённых чанков. На новой машине флаг `--rebuild` не нужен. Если коллекция
   уже существует, импорт остановится, чтобы не перезаписать её случайно.
5. Если на целевой машине нет локального прокси Telegram на
   `127.0.0.1:3066`, отключите его для текущего окна PowerShell. Затем
   запустите оба бота или нужный один:

   ```powershell
   $env:TELEGRAM_PROXY = ""
   .\run_bots.bat
   # По отдельности: .\run_vk_bot.bat или .\run_telegram_bot.bat
   ```

Коллекция Qdrant остаётся в Docker volume. Папка `transfer` нужна для переноса
и восстановления; после успешной проверки её можно хранить как резервную копию.
