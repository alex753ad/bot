# Архитектура проекта

Торговый бот для Binance Futures мониторинга и анализа уровней + Live Bybit торговля (Strategy2).

**Актуализировано:** 19 июня 2026.

**Новое (19 июня):**
- Market phase detection (pump/flat/bleed/drop_tradeable)
- Улучшенная ML модель (6 признаков, clf2 для скорости breakout, hard-filter по touches)
- Strategy2SignalFilter с чистой фильтрацией (без БД, только анализ)
- Поля ML в history.db (p_bounce, expected_depth, ml_delta, p_fast_breakout)
- Trade activity метрики (trades_per_min_1m/5m/15m, trades_increasing)

---

## Общая архитектура

```
Binance Futures (исторические данные и мониторинг)
  │
  ├─ REST klines (1m, 5m, 15m)
  │
  ├─ WebSocket aggTrades (delta tracking)
  │
  └─→ data/collector.py
       │
       ├─→ candles_1m, candles_5m, candles_15m (в памяти)
       │
       ├─→ agg_trades (буфер последних 60 сек сделок)
       │
       └─→ main.py (главный оркестратор)
            │
            ├─ _trigger_loop (проверка роста на 15m, каждые 5 сек)
            │   │
            │   ├─ analysis/level_builder.py (построение уровней)
            │   │
            │   ├─ analysis/trigger.py (ATR, approach_style)
            │   │
            │   ├─ analysis/pump_phase.py (health score пампа)
            │   │
            │   ├─ analysis/ml_score.py (ML: p_bounce, expected_depth, ml_delta)
            │   │
            │   ├─ analysis/market_phase.py (PUMP/FLAT/BLEED/DROP_TRADEABLE)
            │   │
            │   └─ analysis/monitor.py (запуск мониторинга)
            │        │
            │        ├─ Proximity alert → Telegram
            │        │
            │        ├─ Pressure, Volume spike → Telegram
            │        │
            │        ├─ Sweep, Bounce/Breakout events
            │        │
            │        ├─ Trade activity metrics (trades_per_min_*)
            │        │
            │        └─→ trading/event_bus.py
            │             │
            │             ├─→ trading/strategy_runner.py (paper trading)
            │             │    │
            │             │    ├─ S1 Bounce (paper)
            │             │    ├─ S3 Breakout (paper)
            │             │    ├─ S4 Breakout Long (paper)
            │             │    │
            │             │    └─→ trading/trade_log.py → trades.db
            │             │
            │             └─→ trading/strategy2_live.py (LIVE BYBIT)
            │                  │
            │                  ├─ strategy2_signal_filter.py (чистая фильтрация)
            │                  │
            │                  ├─ bybit_client.py (REST подпись)
            │                  │
            │                  └─→ live_trade_log.py → live_trades.db
            │
            ├─ _monitor_health_loop (проверка NATR, активности, каждые 60 сек)
            │
            ├─ _stale_monitors_cleanup (удаление мёртвых мониторов, каждые 30 сек)
            │
            ├─ _refresh_ml_loop (переобучение ML, каждые 30 минут)
            │
            └─ web_server.py + dashboard.html (Real-time UI)

Bybit Demo (LIVE торговля — только Strategy2Live)
  │
  └─→ /v5/order/create (лимит/рыночные ордера)
       /v5/order/cancel (отмена)
       /v5/position/list (получение позиции)
       /v5/execution/list (история исполнения)
       ...
```

---

## Структура файлов

### Корневой уровень
```
mark/
  .env.example                  # шаблон переменных окружения
  requirements.txt              # зависимости
  Procfile                      # Railway deployment
  
  # Данные
  tokens.json                   # активные символы для мониторинга
  blacklist.json                # запрещённые символы
  trigger_times.json            # cooldown триггеров (символ → timestamp)
  active_monitors.json          # восстановление мониторов при рестарте
  history.db                    # исходы уровней, события, ML данные
  trades.db                     # paper trading сделки и статистика
  live_trades.db                # live Bybit сделки (отдельно)
  
  # Основные модули
  main.py                       # оркестратор: trigger, monitor, cleanup, ML loops
  config.py                     # env, пути, TokenRegistry, BlacklistRegistry
  constants.py                  # пороги, интервалы, настройки стратегий
  logger.py                     # loguru конфигурация
  utils.py                      # вспомогательные функции (ATR, approach_style)
  
  # ML & анализ
  train_ml.py                   # обучение clf (p_bounce), reg (depth), clf2 (fast breakout)
  ml_health_check.py            # проверка моделей: MAE, calibration, бакеты
```

### data/ — сбор и хранение данных
```
data/
  collector.py                  # Binance AsyncClient: свечи 1m/5m/15m, aggTrades
  history.py                    # SQLite: уровни, события, профили + ML поля
```

### analysis/ — анализ уровней и мониторинг
```
analysis/
  level_builder.py              # построение уровней (pump_base, body, wick, etc.)
  trigger.py                    # проверка триггера, ATR, approach_style
  monitor.py                    # live мониторинг (proximity, pressure, sweep, bounce/breakout)
  screener.py                   # поиск монет по объёму и росту
  pump_phase.py                 # detect_pump_peak(), health_score(), phase detection
  market_phase.py               # detect_market_phase(): PUMP/FLAT/BLEED/DROP_TRADEABLE
  ml_score.py                   # apply_ml_to_level(): p_bounce, expected_depth, ml_delta
  chart.py                      # PNG график уровня (свечи + VOL + MA20 + POC)
  
  ml/                           # обученные модели (lazy-loaded)
    clf.pkl                     # classifier: bounce vs breakout
    clf2.pkl                    # classifier: fast breakout (touches ≤ 3) vs slow
    reg.pkl                     # regressor: expected_depth (%)
    label_encoder.pkl           # label encoder для типов уровней
    level_type_map.pkl          # маппинг типов уровней → индексы
    thresholds.json             # пороги THRESHOLD_HIGH и THRESHOLD_LOW
```

### ai/ — внешние AI сервисы
```
ai/
  claude_client.py              # Anthropic API для оценки силы уровня
```

### bot/ — Telegram интеграция
```
bot/
  telegram.py                   # aiogram v3: команды, FSM, графики, уведомления
```

### trading/ — стратегии и trade execution
```
trading/
  # Paper Trading
  event_bus.py                  # async очередь событий monitor → strategies
  base_strategy.py              # абстрактный класс paper стратегий
  strategy1_bounce.py           # S1 Bounce от уровня (с trailing)
  strategy3_breakout.py         # S3 Breakout short (с NATR фильтром)
  strategy4_breakout_long.py    # S4 Breakout long (self-scanner)
  strategy_runner.py            # запуск стратегий, price loop, timeout checker
  trade_log.py                  # SQLite CRUD для trades.db
  price_tracker.py              # отслеживание цены после выхода
  
  # Live Bybit Trading (только S2)
  strategy2_live.py             # Strategy2Live: слушает event_bus, выставляет ордера
  strategy2_signal_filter.py    # SignalFilter: чистая фильтрация + расчёт SL/TP
  strategy2_limit_grid.py       # (reference) параметры сетки и алгоритм заполнения
  bybit_client.py               # REST client с подписью HMAC-SHA256
  live_trade_log.py             # SQLite CRUD для live_trades.db
```

---

## Ключевые компоненты

### 1. Data Collection (data/collector.py)

**Входы:**
- Binance Futures REST: свечи 1m, 5m, 15m (до 300 шт.)
- Binance WebSocket: aggTrades для расчёта delta

**Состояние:**
```python
candles_15m: dict[str, list[dict]]  # {symbol: [candle]}
candles_5m: dict[str, list[dict]]
candles_1m: dict[str, list[dict]]
agg_trades: dict[str, list[dict]]   # {symbol: [{"ts", "qty", "is_buy"}]}
```

**Экспорт:** доступны через `from data.collector import candles_1m, ...`

---

### 2. Main Orchestrator (main.py)

**Петли:**

1. **_trigger_loop** (каждые 5 сек)
   - Проверяет условие триггера (рост ≥ 3% за 15m, cooldown)
   - Вызывает level_builder → список уровней
   - Для каждого уровня: расчёт ATR, pump health, ML score, market phase
   - Фильтрует по силе, p_bounce
   - Запускает monitor для подходящих уровней

2. **_monitor_health_loop** (каждые 60 сек)
   - Проверяет NATR(5m) и trades в каждом мониторе
   - Удаляет неактивные мониторы

3. **_stale_monitors_cleanup** (каждые 30 сек)
   - Удаляет мониторы старше 1 часа
   - Сохраняет состояние в active_monitors.json

4. **_refresh_ml_loop** (каждые 30 минут)
   - Переобучает ML модели из history.db
   - Hot-reload в ml_score.py

5. **Telegram bot loop**
   - Команды: `/add`, `/remove`, `/list`, `/monitors`, `/analyze`, `/check`, `/s2_live_on`, `/s2_live_off`, и т.д.

6. **Web server loop** (aiohttp на 127.0.0.1:8080)
   - `/api/state`, `/api/events`, `/api/open-trades`, `/api/open-live-trades`, `/api/trades-history`

---

### 3. Level Building (analysis/level_builder.py)

**Алгоритм:**
1. Поиск pump leg (рост ≥ 5%)
2. Классификация уровней (pump_base, body_level, wick_level, order_block, consolidation)
3. Кластеризация близких уровней
4. Присвоение силы (через Claude или константы)

**Выход:**
```python
{
    "level": float,
    "type": str,                 # pump_base | body_level | wick_level | ...
    "side": str,                 # support | resistance
    "strength": int,             # 1–5 (до ML)
    "pump_price_range": float,
    "position_in_range": float,
    "cluster_id": int,
    "touches": int,
    "approach_style": str,       # flash | impulse | bleed | unknown
    "atr_ratio": float,
    "vol_ratio": float,
}
```

---

### 4. Market Phase Detection (analysis/market_phase.py)

**Определяет:** PUMP | FLAT | BLEED | DROP_TRADEABLE | UNKNOWN

**Вход:** последние 1m и 5m свечи, ATR

**Алгоритм:**
1. Расчёт диапазона (90 свечей 1m)
2. Direction efficiency (направленность за 60 свечей)
3. Pump detection: цена ↑ ≥ 5% за 40m + atr_ratio ≥ 1.8
4. Flat detection: диапазон < 4% + dir_eff < 0.25
5. Bleed vs Tradeable drop: качество отскоков по 5m-свечам

**Выход:**
```python
PhaseResult(
    phase=MarketPhase,           # enum
    range_low, range_high,       # float
    direction_efficiency,        # 0.0–1.0
    bounce_quality,              # 0.0–1.0
    swing_low,                   # float
    note,                        # str для логов
)
```

---

### 5. ML Scoring (analysis/ml_score.py)

**3 модели:**
1. **clf** — RandomForest классификатор вероятности отбоя (bounce vs breakout)
2. **clf2** — опциональный классификатор скорости breakout (fast ≤3 touches vs slow)
3. **reg** — регрессор ожидаемой глубины прокола под уровень (%)

**6 признаков (touches убран, закрыт hard-filter):**
```
1. strength           — 1–5
2. ltype_enc          — тип уровня
3. vol_ratio          — текущий объём / среднее (обрезано до 20)
4. atr_ratio          — расстояние до уровня в ATR (обрезано до 20)
5. style_enc          — стиль подхода (flash=0, impulse=1, bleed=2, unknown=3)
6. age_capped         — время мониторинга в мин (обрезано до 300)
```

**Hard-filter:** `touches ≥ 2 → p_bounce = 0.0, ml_delta = -2` (детерминировано из истории: 0% bounce при touches≥2)

**Пороги (откалиброваны по P25/P75):**
- `p_bounce ≥ 0.97` → ml_delta = +1
- `p_bounce ≤ 0.60` → ml_delta = -1
- иначе → ml_delta = 0

**Выход:**
```python
{
    "p_bounce": float,           # 0.0–1.0
    "expected_depth": float,     # % (консервативное, is_breakout=0)
    "ml_delta": int,             # +1 / 0 / -1 / -2
    "p_fast_breakout": float,    # 0.0–1.0 (None если clf2 не загружен)
}
```

---

### 6. Monitor (analysis/monitor.py)

**Функция:** `start_monitor(symbol, level, level_side, ...)`

**События:**
1. **Proximity** — цена в пределах 2% от уровня → Telegram
2. **Pressure** — 3+ направленных свечи 15m с объёмом → Telegram
3. **Volume spike** — объём ×3.0 → Telegram (5 мин cooldown)
4. **Sweep** — прокол под уровень → расчёт выкупа, Telegram
5. **Bounce** — отбой после прокола → event_bus, log в history.db
6. **Breakout** — подтверждённый пробой (объём ≥ 2.4x) → event_bus, log в history.db
7. **Level broken** — 5+ свечей ниже уровня → Telegram

**Trade activity метрики (при касании):**
```python
trades_per_min_1m    — количество сделок в последней 1m свече
trades_per_min_5m    — среднее за последние 5 1m свечей
trades_per_min_15m   — среднее за последние 15 1m свечей
trades_increasing    — 1 если 1m > 5m > 15m, иначе 0
```

**Запись в history.db (при завершении monitoring):**
```python
save_level_outcome(
    symbol, level, level_type, strength, approach_type, vol_ratio, touches,
    outcome,                   # bounce | breakout | partial | no_reach
    approach_style,            # flash | impulse | bleed | unknown
    vol_ratio_at_touch,
    atr_ratio,
    fill_depth_pct,
    monitoring_age_minutes,
    trades_per_min_1m, trades_per_min_5m, trades_per_min_15m, trades_increasing,
    p_bounce_at_entry,         # из ML
    expected_depth_at_entry,   # из ML
    ml_delta_at_entry,         # из ML
    p_fast_breakout_at_entry,  # из ML clf2
)
```

---

### 7. Strategy2 Live Trading

**Точка входа:** `strategy2_live.py` слушает event_bus

**Фильтрация сигналов:** `strategy2_signal_filter.py`
- Проверяет: strength ≥ 3, p_bounce ≥ 0.60, market phase (не BLEED)
- Рассчитывает: SL (ниже range_low для FLAT, ниже swing_low для DROP), TP1, TP2
- Возвращает: go/no_go + параметры сетки

**Grid параметры:**
```python
grid_width = atr × 2.5
step = grid_width / 9  # S2_GRID_ORDERS = 10
entry_prices = [level + step, level, level - step, ...]
order_size = 20 USDT per order
```

**Управление позицией:**
- Размещает лимит-ордера на сетке
- Отслеживает заполнение через Bybit API
- Выставляет стоп-лимит SL при первом заполнении
- Trailing TP при приближении к TP1

**БД:** live_trades.db с полями grid_orders_json, bybit_order_ids_json, pnl_usdt

---

### 8. Training ML Models (train_ml.py)

**Входы:** history.db → уровни с исходами (bounce/breakout) и всеми признаками

**Процесс:**
1. Фильтрация: outcome IN ('bounce', 'breakout'), strength ≠ 0
2. Сплит: train (80%) / test (20%)
3. Обучение clf (RandomForest), clf2 (опциональный), reg
4. Калибровка порогов по P25/P75
5. Сохранение в analysis/ml/

**Метрики (выводит в консоль):**
- Train/Test accuracy для clf
- Brier score (калибровка)
- ROC-AUC
- MAE для регрессора
- Feature importance

---

### 9. Базы данных

#### history.db (анализ уровней)

**level_outcomes** таблица (с ML полями):
```
symbol, level, level_type, strength_claude,
approach_style,          # flash | impulse | bleed | unknown
atr_ratio, vol_ratio_at_touch,
outcome,                 # bounce | breakout | partial | no_reach
p_bounce_at_entry,       # float 0.0–1.0
expected_depth_at_entry, # float %
ml_delta_at_entry,       # int +1 / 0 / -1 / -2
p_fast_breakout_at_entry,# float 0.0–1.0
fill_depth_pct,
monitoring_age_minutes,
trades_per_min_1m, trades_per_min_5m, trades_per_min_15m, trades_increasing,
...
```

#### trades.db (paper trading)

**trades** таблица:
```
trade_id, strategy_id, symbol, level, entry_price, exit_price,
pnl_usdt, pnl_pct, status, created_at, updated_at, ...
```

#### live_trades.db (live Bybit)

**live_trades** таблица:
```
trade_id, symbol, level, entry_price,
grid_orders_json,       # массив выставленных ордеров
bybit_order_ids_json,   # IDs с Bybit
bybit_position_qty,
grid_fill_count,
status, pnl_usdt, ...
```

---

## Пороги и настройки

### Market Phase
| Параметр | Значение | Описание |
|---|---|---|
| `PHASE_RANGE_WINDOW_1M` | 90 | окно 1m свечей для диапазона (90 мин) |
| `PHASE_PUMP_WINDOW_1M` | 40 | окно для детекции памп (40 мин) |
| `PHASE_PUMP_PRICE_CHANGE_MIN_PCT` | 5.0 | минимальный рост % за pump_window |
| `PHASE_PUMP_ATR_RATIO_MIN` | 1.8 | минимальный atr/baseline_atr |
| `PHASE_FLAT_RANGE_MAX_PCT` | 4.0 | максимальный диапазон % для флета |
| `PHASE_BLEED_BOUNCE_QUALITY_MAX` | 0.35 | ниже → BLEED (не торговать) |
| `PHASE_TRADEABLE_BOUNCE_QUALITY_MIN` | 0.50 | выше → DROP_TRADEABLE |

### ML Model
| Параметр | Значение |
|---|---|
| `THRESHOLD_HIGH` | 0.97 |
| `THRESHOLD_LOW` | 0.60 |
| `TOUCHES_BLOCK` | 2 |

### Strategy2 Live
| Параметр | Значение |
|---|---|
| `S2_MIN_STRENGTH` | 3 |
| `S2_MIN_P_BOUNCE` | 0.60 |
| `S2_GRID_ORDERS` | 10 |
| `S2_POSITION_SIZE_USDT` | 200 |
| `S2_SL_MIN_DIST_ATR` | 0.8 |
| `S2_SL_RR_MIN` | 1.2 |

---

## Запуск

```bash
# Основной бот
python main.py

# Обучение ML моделей
python train_ml.py

# Проверка здоровья ML моделей
python ml_health_check.py --db history.db --min-trades 30

# Экспорт данных
python export_to_csv.py
```

---

## Известные особенности

1. **Market phase определяется в trigger_loop** — используется при фильтрации сигналов для S2
2. **ML модели ленивая загрузка** — первый запрос медленнее, потом кешируются
3. **Hard-filter по touches ≥ 2** — детерминировано из истории, не переопределяется ML
4. **clf2 опциональный** — если clf2.pkl отсутствует, p_fast_breakout = None
5. **Trade activity метрики** — вычисляются в момент касания уровня из поля `trades` в свечах
6. **Live Bybit торговля отдельна** — не использует trade_log.py, ведёт live_trades.db
7. **Event bus асинхронная очередь** — события обрабатываются в порядке подписки

---

## Точки расширения

- **Новые события мониторинга:** добавить функцию в monitor.py, опубликовать в event_bus
- **Новые стратегии paper:** наследуют BaseStrategy, реализуют on_event() и _check_exit()
- **Новые ML модели:** обучить в train_ml.py, добавить загрузку и инференс в ml_score.py
- **Дополнительные анализы:** добавить в analysis/, вызывать из main.py
