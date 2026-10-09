# Веб-интерфейс DocPipe

После запуска API откройте `http://localhost:8000/`. Страница позволяет отправить PDF или изображение, следить за обработкой и скачать архив с результатами. JSON, Markdown, HTML, потоковый PDF, PDF с текстовым слоем, изображения, отчёт проверки и диагностические метрики входят в архив всегда; DOCX можно выбрать дополнительно.

## Запуск через Docker Compose

Нужны Docker Desktop и доступ к интернету на этапе первой сборки образа: сборка устанавливает Docling и загружает модели.

В PowerShell из корня проекта:

```powershell
docker compose -f deploy/docker-compose.yml up --build -d
```

Дождитесь готовности API и откройте интерфейс:

```powershell
Invoke-RestMethod http://localhost:8000/readyz
Start-Process http://localhost:8000/
```

Ключ для обработки через веб-интерфейс не требуется. Результат скачивается ZIP-архивом.

Входные форматы: PDF, PNG, JPG/JPEG, TIFF, BMP и WEBP. Эталонный DOCX не является входным сканом; его можно использовать для последующей оценки через `docpipe eval`.

Чтобы остановить сервис:

```powershell
docker compose -f deploy/docker-compose.yml down
```

Для отдельного API с обязательной авторизацией задайте `DOCPIPE_API__REQUIRE_API_KEY=true` и `DOCPIPE_API__API_KEY` (не менее 32 символов). Веб-интерфейс рассчитан на обычный режим без обязательного ключа.
