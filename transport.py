"""Bot API transport. Never include request URLs, payloads or tokens in errors."""
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code=0, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f'Telegram {code}' if code else 'Telegram: сетевая ошибка')


class Telegram:
    def __init__(self, token):
        self._base = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, payload):
        req = urllib.request.Request(self._base + method,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                body = json.load(response)
        except urllib.error.HTTPError as e:
            retry = 0
            try:
                body = json.load(e)
                retry = int(body.get('parameters', {}).get('retry_after', 0))
            except (ValueError, TypeError, OSError):
                pass
            raise ApiError(e.code, retry) from None
        except (OSError, ValueError):
            raise ApiError() from None
        if not body.get('ok'):
            raise ApiError(body.get('error_code', 0), body.get('parameters', {}).get('retry_after', 0))
        return body['result']


def drain(store, api, allowed, at=None):
    """Deliver durable effects. On uncertain network failure, retry without marking sent."""
    at = time.time() if at is None else at
    rows = store.pending(at)
    # Stop Telegram's button spinner before delivering potentially long lists of messages.
    rows.sort(key=lambda r: (r['method'] != 'answerCallbackQuery', r['id']))
    blocked = {r[0] for r in store.db.execute(
        "SELECT json_extract(payload,'$.chat_id') FROM outbox WHERE retry_at>? AND error IS NULL", (at,))}
    for row in rows[:50]:
        payload = row['payload']
        owner = payload.get('chat_id')
        if owner in blocked:
            continue
        if owner is not None and owner not in allowed:
            with store.db:
                store.failed(row['id'], error='Access revoked')
            continue
        try:
            api.call(row['method'], payload)
        except ApiError as e:
            if e.code in (400, 401, 403, 404):
                # Retain a diagnostic row, without repeatedly sending invalid/forbidden requests.
                with store.db:
                    store.failed(row['id'], error=str(e))
                log.warning('Не доставлено сообщение #%s: %s', row['id'], e)
                if e.code == 401:
                    raise
            else:
                delay = max(e.retry_after, min(300, 2 ** min(row['attempts'] + 1, 8)))
                with store.db:
                    store.failed(row['id'], retry_at=at + delay)
                log.warning('Отправка #%s отложена: %s', row['id'], e)
                if owner is not None:
                    blocked.add(owner)
                if e.code == 0 or e.code >= 500 or e.code == 429:
                    break
        else:
            with store.db:
                store.delivered(row['id'])

