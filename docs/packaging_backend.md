# Backend упаковки (A2): FSM, шаги и API

Этот документ объясняет, **как устроен упаковочный backend** и почему он работает именно так.
Задача — чтобы новый разработчик мог быстро понять **инварианты процесса** и не сломать
цепочку упаковки при дальнейших изменениях.

## 1) Краткая схема процесса

```
START -> LAYOUT (шаги выкладки) -> PHASE_NEXT -> PACKING (шаги упаковки)
   -> CLOSE_BOX -> PRINT_LABEL -> TABLE_EMPTY -> START (следующий SKU)
```

**Ключевой принцип:** новый SKU можно начинать **только после** события `TABLE_EMPTY`.
Это наш "gate", который гарантирует, что предыдущая упаковка физически завершена
и рабочий стол действительно освобождён.

## 2) FSM упаковки: состояния и переходы

FSM хранится в `services/packaging.py` и управляет событиями и ограничениями.

| Текущее состояние | Разрешённые события | Новое состояние |
| --- | --- | --- |
| `None` | `START` | `started` |
| `table_empty` | `START` | `started` |
| `started` | `BOX_CLOSED`, `TABLE_EMPTY` | `box_closed` / `table_empty` |
| `box_closed` | `PRINT_LABEL`, `TABLE_EMPTY` | `label_printed` / `table_empty` |
| `label_printed` | `TABLE_EMPTY` | `table_empty` |

**Почему так:** мы допускаем `TABLE_EMPTY` даже до закрытия коробки, чтобы
обеспечить корректное "размыкание" процесса при ошибках на линии. При этом
начать новый SKU нельзя, пока состояние **не `table_empty`**.

### Ошибки 409 (Conflict)

Ошибки вида `409 Conflict` появляются, когда нарушается допустимый переход.
Пример: `PRINT_LABEL` без `BOX_CLOSED` или `START` без `TABLE_EMPTY` после
предыдущего SKU.

## 3) Gate "TABLE_EMPTY" — зачем он нужен

Без подтверждения `TABLE_EMPTY` оператор может начать новый SKU,
когда предыдущая коробка ещё находится на столе. Это риск:

1. **Смешивание деталей** (новые элементы попадают в старую коробку).
2. **Потеря аудита** (события относятся к неверному SKU).
3. **Неверная маркировка** (label печатается для "старого" SKU).

Поэтому `TABLE_EMPTY` — обязательная точка синхронизации между SKU.

## 4) Workflow шагов: LAYOUT -> PACKING

Для каждого SKU строится простой план шагов:

- **LAYOUT** — выкладка деталей по слотам.
- **PACKING** — упаковка в обратном порядке (инвариант: последовательность обратная).

Параметры прогресса хранятся в `pack_sessions`:

| Поле | Значение |
| --- | --- |
| `phase` | Текущая фаза (`LAYOUT` или `PACKING`) |
| `current_step_index` | Индекс шага, который нужно выполнить сейчас |
| `total_steps` | Количество шагов в текущей фазе |

## 5) Pack events: типы и payload

События пишутся в `pack_events`. Они нужны для аудита и аналитики.

| event_type | Когда пишется | payload_json |
| --- | --- | --- |
| `START` | старт упаковки | пусто |
| `BOX_CLOSED` | закрытие коробки | пусто |
| `PRINT_LABEL` | печать этикетки | пусто |
| `TABLE_EMPTY` | подтверждение пустого стола | пусто |
| `STEP_COMPLETED` | завершение шага | `step_id`, `phase`, `slot`, `part_code`, `verify_result` |
| `PHASE_CHANGED` | переход LAYOUT -> PACKING | `from`, `to` |

**Почему payload_json важен:** он позволяет хранить расширенную контекстную информацию,
не меняя схему базы. Это удобно для будущих CV/ML модулей и аналитики.

## 6) UI flags: как рассчитываются

Флаги вычисляются строго из FSM (никакой новой логики поверх):

- `can_start_sku`
- `can_mark_table_empty`
- `can_close_box`
- `can_print_label`

Это сделано в `compute_pack_ui_flags`, чтобы фронт **не гадал**, что можно делать.

## 7) API: примеры запросов

### Старт SKU

```bash
curl -X POST http://localhost:8000/api/kiosk/pack/start \\
  -H 'Content-Type: application/json' \\
  -d '{"sku":"SKU-1"}'
```

### Получить UI-состояние

```bash
curl http://localhost:8000/api/kiosk/pack/ui-state
```

Пример ответа:

```json
{
  "active_session": {
    "id": 12,
    "shift_id": null,
    "worker_id": null,
    "sku": "SKU-1",
    "state": "started",
    "start_time": 1710000000.0,
    "end_time": null,
    "phase": "LAYOUT",
    "current_step_index": 0,
    "total_steps": 3
  },
  "pack_state": "started",
  "can_start_sku": false,
  "can_mark_table_empty": false,
  "can_close_box": true,
  "can_print_label": false
}
```

### Состояние шагов

```bash
curl http://localhost:8000/api/kiosk/pack/steps/state
```

Пример ответа:

```json
{
  "status": "ok",
  "phase": "LAYOUT",
  "current_step_index": 1,
  "total_steps": 3,
  "current_step": {
    "step_id": "layout-1",
    "phase": "LAYOUT",
    "index": 1,
    "slot": "A2",
    "part_code": "PART-2"
  }
}
```

### Завершить шаг

```bash
curl -X POST http://localhost:8000/api/kiosk/pack/step/complete
```

### Перейти к PACKING

```bash
curl -X POST http://localhost:8000/api/kiosk/pack/phase/next
```

## 8) Типичные ошибки 409 и объяснение

### `start` без `TABLE_EMPTY`
**Ответ:** `409 Conflict`  
**Причина:** предыдущий SKU ещё не подтверждён как завершённый.

### `phase/next` до завершения LAYOUT
**Ответ:** `409 Conflict`  
**Причина:** упаковка должна идти строго после выкладки всех слотов.

### `print-label` до `close-box`
**Ответ:** `409 Conflict`  
**Причина:** этикетка печатается только после закрытия коробки.
