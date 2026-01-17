# Автозапуск KZ Kiosk через systemd

## Что это

Эта инструкция описывает стандартный запуск backend-киоска через systemd, чтобы после перезагрузки сервис поднимался автоматически и отвечал на `/api/kiosk/state`.

## 1. Подготовка проекта и venv

```bash
# Учительская подсказка: проект должен лежать в /opt, чтобы unit совпадал с путями.
sudo mkdir -p /opt
sudo rsync -a --delete ./ /opt/kz-computer-vision/

cd /opt/kz-computer-vision
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Установка systemd unit

```bash
# Учительская подсказка: копируем готовый unit в systemd.
sudo cp /opt/kz-computer-vision/deploy/kz-kiosk.service /etc/systemd/system/kz-kiosk.service
sudo systemctl daemon-reload
sudo systemctl enable kz-kiosk
sudo systemctl start kz-kiosk
```

## 3. Проверка

```bash
systemctl status kz-kiosk
curl -s http://127.0.0.1:8000/api/kiosk/state | head
```

Ожидаем статус `active` и валидный JSON в ответе.

## 4. Настройки (при необходимости)

Если нужно изменить порт/хост, используйте переменные окружения прямо в unit-файле:

```
Environment="KZ_KIOSK_HOST=0.0.0.0"
Environment="KZ_KIOSK_PORT=8000"
```

После изменения:

```bash
sudo systemctl daemon-reload
sudo systemctl restart kz-kiosk
```
