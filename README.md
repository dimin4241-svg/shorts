# Shorts Factory 2.0 — «Невидимый мир»

Оригинальная Python-студия русскоязычных научно-популярных Shorts, Reels и TikTok: исследование → проверенные сценарии → аудиопревью → анимация → субтитры → проверка MP4 → анализ опубликованных результатов. Главный критерий — понятная история и измеренный интерес аудитории. Эвристический рейтинг не предсказывает просмотры. Автопубликации и искусственной активности нет.

Самостоятельная MIT-реализация. Код [Animator](https://github.com/ashishjamarkattel/Animator) не копируется и не включён: отсутствие лицензии у upstream не даёт права переносить его код.

## Быстрый запуск на macOS Apple Silicon

```bash
git clone https://github.com/dimin4241-svg/shorts.git
cd shorts
brew install ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Беззвучный технический ролик одной командой, **без платных API**:

```bash
.venv/bin/python -m shorts_factory --pilot --demo --max-cost-usd 0
```

Предпочтительный голос пользователя — Microsoft `ru-RU-DmitryNeural`. Для озвученного ролика настройте `AZURE_SPEECH_KEY` и `AZURE_SPEECH_REGION` в `.env`, затем явно задайте лимит расходов:

```bash
.venv/bin/python -m shorts_factory --pilot --voice-provider azure \
  --voice ru-RU-DmitryNeural --max-cost-usd 1
```

Системный голос Milena пользователь прослушал и отклонил как роботизированный; используйте его только для проверки сборки. Подключение Azure и бесплатный местный нейросинтез описаны в [проверке голосов](docs/VOICE_QUALITY.md). Беззвучный тест работает и на Linux:

```bash
.venv/bin/python -m shorts_factory --pilot --demo --width 360 --height 640 --fps 12
```

Полный локальный ролик содержит движущиеся оптоволокно, световые пакеты, маршруты и переключение резервной линии. Это оригинальная объясняющая графика, не документальная запись повреждения кабеля. Системный голос и оценочные тайминги требуют прослушивания перед публикацией.

## Сценарии и стили

[CONTENT_PLAN.md](CONTENT_PLAN.md) содержит не менее 15 идей, прозрачные оценки и пять полных пакетов. [Отчёт исследования](docs/TREND_REPORT.md) фиксирует дату, источники, доступность метрик и ограничения. Сценарии и факты поставляются в `shorts_factory/content/` и доступны после установки пакета.

```bash
# Подводные кабели: варианты на 20, 28 (по умолчанию), 40 секунд
.venv/bin/python -m shorts_factory --pilot submarine-cables --seconds 28 --voice-provider azure --style explainer --max-cost-usd 1
# Список ID: python -c 'from shorts_factory.script_engine import catalog; print(catalog())'
# Другая атмосфера той же анимации
.venv/bin/python -m shorts_factory --pilot --voice-provider azure --style cinematic --fps 30 --max-cost-usd 1
```

`EXPLAINER` — чистые контрастные схемы. `CINEMATIC` — глубокая палитра, атмосферный свет и параллакс; локальный движок не выдаёт это за настоящее сгенерированное 3D-видео. Сохранены `whiteboard`, `infographic`, `cartoon`, OpenAI Images и Claude SVG. API-картинки остаются статическими иллюстрациями с местной анимацией; Claude пишет векторный код, не фотографии. SVG проходит проверку допустимых элементов и не исполняет скрипты.

Рендер поддерживает 1080×1920, H.264/AAC, 24/30 fps (низкое разрешение и 12 fps для быстрых тестов). Длины сцен рассчитываются по аудио после удаления только длинной пустой тишины в конце клипов. Начала и паузы внутри фраз сохраняются, текст и темп не меняются. Если речь всё равно длиннее цели, в отчёте появляется замечание: сократите сценарий или пересинтезируйте конкретную сцену.

Для собственного проверенного сценария используйте `--storyboard story.json --facts-json facts.json`. Формат storyboard сохранён: `title`, `description`, 3–7 `scenes` с `narration`, `caption`, `image_prompt`. `facts.json` — массив утверждений с `sources`. Произвольный текст от языковой модели не считается проверенным только потому, что он записан в JSON.

## Голоса и порядок производства

Сначала прослушайте хук:

```bash
.venv/bin/python -m shorts_factory --voice-provider yandex --voice ermil \
  --voice-preview 'Почему интернет между материками идёт по дну океана?' --max-cost-usd 0.10
.venv/bin/python -m shorts_factory --voice-provider elevenlabs \
  --voice-preview 'Почему интернет между материками идёт по дну океана?' --max-cost-usd 0.10
```

Для Yandex нужны `YANDEX_API_KEY` сервисного аккаунта и доступ к SpeechKit; основной кандидат — `ermil`. Для ElevenLabs нужны `ELEVENLABS_API_KEY` и реальный русскоязычный `ELEVENLABS_VOICE_ID`, модель `eleven_multilingual_v2`. Без ключей эти команды завершаются понятной ошибкой, а не переходят к чужим сервисам. `macos` и `none` сохранены. OpenAI TTS оставлен для совместимости.

Для Microsoft «Дмитрий»: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `--voice-provider azure --voice ru-RU-DmitryNeural` и явный бюджет. `--voice-provider local` запускает необязательный подготовленный Qwen3 VoiceDesign на Apple Silicon без платного API. Его оригинальный тембр тоже сначала нужно прослушать. [Инструкции и условия голосов](docs/VOICE_QUALITY.md).

Официальная документация проверена 26.10.2026: [Yandex v1](https://aistudio.yandex.ru/ru/docs/speechkit/tts/request), [ElevenLabs Create speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [OpenAI TTS](https://developers.openai.com/api/docs/guides/text-to-speech). Для OpenAI `gpt-4o-mini-tts`/`tts-1`/`tts-1-hd` заявлено отключение 06.01.2027. Миграция требует Realtime API; простой замены имени модели в `/v1/audio/speech` недостаточно. Здесь API самовольно не меняется.

```bash
# Сохранить речь всех сцен до любых изображений
.venv/bin/python -m shorts_factory --pilot --voice-provider yandex --voice ermil \
  --audio-only --max-cost-usd 1 --max-voice-cost-usd 1
# После прослушивания продолжить ТОТ ЖЕ каталог v1, повторив настройки голоса:
.venv/bin/python -m shorts_factory --pilot --voice-provider yandex --voice ermil \
  --resume runs/ИМЯ-ЗАПУСКА/v1 --max-cost-usd 1
```

Для платных изображений дополнительно требуется `--voice-reviewed`: подтверждение прослушивания подготовленного аудио. Скрипт, все аудиофайлы и их длительности создаются и проверяются **до** запросов к изображениям.

```bash
# Опционально заменить местную графику на OpenAI-иллюстрации с анимацией
.venv/bin/python -m shorts_factory --pilot --voice-provider macos --audio-only
.venv/bin/python -m shorts_factory --pilot --voice-provider macos \
  --resume runs/ИМЯ-ЗАПУСКА/v1 --image-provider openai --voice-reviewed \
  --max-cost-usd 1 --max-images-cost-usd 1
# Аналогично: --image-provider claude-svg и ANTHROPIC_API_KEY
```

`--glossary glossary.json` сохраняет явный словарь замен и ударений; образец — `glossary.example.json`. Не заменяйте автоматически каждую «е» на «ё». Yandex `+` остаётся только в тексте синтеза и не попадает в субтитры. `--speech-speed` задаёт скорость синтеза вручную; видеомонтаж её не меняет. ElevenLabs получает соседние фразы для связности. Итоговый мастер: −16 LUFS, потолок −1.5 dBTP, AAC 48 кГц; проверка измеряет результат после кодирования. Музыка выключена, чужих треков или видеозаписей нет.

## Расходы и повторное использование

По умолчанию `--max-cost-usd 0`: платные операции заблокированы. `--dry-run` (также `--plan-only`) ничего не генерирует и не вызывает API. Лимиты `--max-research-cost-usd`, `--max-scripts-cost-usd`, `--max-images-cost-usd`, `--max-voice-cost-usd` действуют внутри общего лимита **на все варианты запуска**.

`cost_ledger.json` резервирует приблизительную стоимость перед каждым запросом, сохраняет провайдера, этап и статус. Значения .08 USD/сценарий, .12 USD/изображение, .30 USD/1000 символов речи — консервативные плановые допущения, **не тарифы и не подтверждённые счета**. Они не могут гарантировать реальную сумму списания: проверьте текущие тарифы и задайте ограничения у провайдера. Исследование YouTube расходует квоту, а не резерв платного синтеза. Автоматических повторов платных запросов нет: при таймауте списание может уже произойти, поэтому резерв сохраняется.

`--resume` повторно использует только файлы с совпадающими параметрами, текстом и SHA-256. Изменение текста, модели, словаря, голоса или повреждение файла обновляет соответствующий материал. Необходимые результаты промежуточных этапов сохраняются при ошибке. Для одного изменённого кадра/субтитра достаточно повторного рендера с прежним аудио и изображениями. Секреты не входят в метаданные и хеши настроек.

## Субтитры и контроль качества

`caption_engine` экспортирует SRT и ASS, короткие фразы, кириллицу и консервативную общую безопасную зону: 8–85% ширины, 10–80% высоты. Надписи автоматически вписываются, их геометрия сохраняется. Для платформенного интерфейса всё равно нужен визуальный просмотр.

Без выравнивания фразы распределяются **оценочно** по реальной длительности синтеза; это не точные акустические метки. Подсветка слов включается только с `--word-timings alignment.json`. Импорт должен иметь `source: transcription|forced_alignment|manual` и `scenes: [{words: [{word,start,end}, ...]}, ...]`; секунды относительно начала сцены. Проверяются текст, порядок, конечность чисел и границы; сам файл не доказывает точность распознавания. Платный ASR скрыто не вызывается.

После рендера `video_quality_gate` полностью декодирует видео и звук и создаёт `quality_report.json`: геометрия, потоки/кодеки, длительность, чёрные кадры, длинная тишина, LUFS/true peak, совпадение текста субтитров, источники, происхождение материалов и непрерывность таймлайна. При критическом сбое CLI возвращает ненулевой код и указывает `repair_stages`.

Прослушивание ударений, разборчивости, щелчков, фактическое совпадение субтитров с речью и редакционная оценка остаются `review`. Наличие ссылок не доказывает фактологическую истинность; отсутствие чёрных кадров не доказывает качество монтажа. Статус `needs_human_review` не является разрешением на публикацию.

```bash
.venv/bin/python -m shorts_factory --quality-check runs/ИМЯ-ЗАПУСКА/v1/short.mp4
.venv/bin/python -m pytest -q
```

## Исследование и реальные результаты

```bash
# Без ключа создаёт честный отчёт о недоступных данных
.venv/bin/python -m shorts_factory --research
# Официальный публичный Google Trends RSS — явный opt-in
.venv/bin/python -m shorts_factory --research --google-trends-rss
# Можно передать разрешённые CSV-экспорты
.venv/bin/python -m shorts_factory --research --google-trends-csv trends.csv --tiktok-csv tiktok.csv
.venv/bin/python -m shorts_factory --metrics-csv studio.csv --metrics-observed-at 2026-10-26T10:00:00+05:00
```

`YOUTUBE_API_KEY` включает официальные YouTube Data API запросы по 72 часам, 7 и 30 дням, русскому языку и короткой длительности. Это выборка результатов поиска, не полный рынок Shorts; `videoDuration=short` не доказывает формат Shorts. Снимок накопленных просмотров не показывает динамику; сохраняйте следующие снимки для сравнения. Google Trends geo=RU — поиски в стране, не все русскоязычные зрители. TikTok Creative Center используется через разрешённые ручные экспорты; закрытые endpoint, капчи и авторизация не обходятся. Подробности — [методика исследования и аналитики](docs/RESEARCH_METHODS.md).

`performance_lab` читает английские/русские CSV Studio, разделяет обычные `views` и `engaged_views`, сохраняет недоступные метрики как `null`, не подменяет «показано в ленте» показами миниатюр. Сравнивает только данные с известными сопоставимыми длительностью, возрастом публикации и периодом наблюдения. Малые выборки дают предупреждения, а не вывод о победившем хуке. Поддержан явный opt-in импорт официального Analytics API через Python; OAuth-токен передаётся вызывающим приложением и не сохраняется.

## Артефакты

В `runs/<запуск>/v1/`: `storyboard.json`, `facts.json`, `editorial_plan.json` (пилоты), `audio_*.aiff|mp3`, изображения (при выборе API), `short.mp4`, `captions.srt`, `subtitles.srt`, `captions.ass`, `caption_manifest.json`, `geometry.json`, `timeline.json`, `provenance.json`, `metadata.json`, `quality_report.json`, PNG-превью. В корне запуска — `cost_ledger.json`. `runs/`, `.env`, виртуальное окружение и тяжёлые медиа не попадают в Git.

Проверки провайдеров используют моки: реальные платные вызовы требуют ключей и явного запуска. Шрифт можно задать через `SHORTS_FONT=/absolute/path/to/cyrillic.ttf`. Для Claude SVG на macOS при отсутствии Cairo: `brew install cairo`; локальная процедурная анимация от Cairo не зависит. Python ≥3.10; внешние утилиты — FFmpeg и ffprobe.
