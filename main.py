import argparse
from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import sys
import time

from bot_stats import Bot
from access import AccessPolicy
from store import Store
from transport import Telegram, ApiError, drain
from views import MOSCOW

ROOT = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


@dataclass
class Settings:
    token: str = field(repr=False)
    allowed: set[int]
    db_path: Path
    admins: set[int] = field(default_factory=set)


def load_settings(root=ROOT):
    values = {}
    envfile = root / '.env'
    if envfile.exists():
        for line in envfile.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('\"\'')
    def value(key, default=''):
        return os.environ.get(key, values.get(key, default)).strip()
    token = value('BOT_TOKEN')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{10,}', token):
        raise ValueError('Добавьте BOT_TOKEN в локальный .env. Не отправляйте токен в чат.')
    raw = value('ALLOWED_USER_IDS')
    public=value('PUBLIC_ACCESS','0')=='1'
    if (not public or raw) and not re.fullmatch(r'[1-9]\d*(\s*,\s*[1-9]\d*)*', raw):
        raise ValueError('Укажите ALLOWED_USER_IDS: положительные Telegram ID через запятую.')
    admin_raw=value('ADMIN_USER_IDS')
    if admin_raw and not re.fullmatch(r'[1-9]\d*(\s*,\s*[1-9]\d*)*',admin_raw):
        raise ValueError('ADMIN_USER_IDS должен содержать числовые ID через запятую.')
    return Settings(token, AccessPolicy({int(x.strip()) for x in raw.split(',') if x.strip()},public),
        root / 'data' / 'calendar.sqlite3',{int(x.strip()) for x in admin_raw.split(',') if x.strip()})


class ProcessLock:
    """OS lock is released even if the process crashes; no stale PID files."""
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        if self.file.seek(0, 2) == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Бот уже запущен в другом процессе.') from None
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


def run(settings, check=False):
    api = Telegram(settings.token)
    # These calls read bot state, they do not delete an existing webhook or publish commands.
    me = api.call('getMe', {})
    webhook = api.call('getWebhookInfo', {})
    if webhook.get('url'):
        raise ValueError('У этого бота уже настроен webhook. Используйте отдельного бота; существующие настройки не менялись.')
    log.info('Подключение успешно: @%s. Часовой пояс: Москва.', me['username'])
    if check:
        return
    with ProcessLock(ROOT / 'data' / 'bot.lock'):
        store = Store(settings.db_path)
        bot = Bot(store, settings.allowed,admins=settings.admins)
        try:
            log.info('Бот работает. Для остановки нажмите Ctrl+C.')
            while True:
                try:
                    bot.queue_reminders(datetime.now(MOSCOW))
                    drain(store, api, settings.allowed)
                    updates = api.call('getUpdates', {'offset': store.offset(), 'timeout': 5, 'limit': 50,
                        'allowed_updates': ['message', 'callback_query']})
                    for update in sorted(updates, key=lambda u: u['update_id']):
                        bot.handle(update, datetime.now(MOSCOW))
                    drain(store, api, settings.allowed)
                except ApiError as e:
                    if e.code in (401, 409):
                        raise ValueError('Токен недействителен или этот бот запущен ещё где-то. Проверьте настройки.') from None
                    log.warning('%s; повтор через несколько секунд.', e)
                    time.sleep(min(30, max(3, e.retry_after)))
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description='Личный календарь записей в Telegram')
    parser.add_argument('--check', action='store_true', help='Проверить токен и webhook без чтения и отправки сообщений')
    parser.add_argument('--backup', action='store_true', help='Сохранить локальную резервную копию, без Telegram')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        if args.backup:
            db = ROOT / 'data' / 'calendar.sqlite3'
            if not db.exists():
                raise ValueError('База записей пока не создана.')
            path = ROOT / 'data' / f'backup-{datetime.now(MOSCOW).strftime("%Y%m%d-%H%M%S-%f")}.sqlite3'
            store = Store(db)
            try:
                store.backup(path)
            finally:
                store.close()
            log.info('Резервная копия: %s', path)
        else:
            run(load_settings(), args.check)
    except KeyboardInterrupt:
        log.info('Бот остановлен. Записи сохранены.')
    except (ValueError, RuntimeError, ApiError) as e:
        log.error('%s', e)
        return 1
    except Exception:
        # Avoid tracebacks that can include Telegram request URLs or private message contents.
        log.error('Непредвиденная ошибка. Проверьте доступ к папке data, свободное место и запустите тесты.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
