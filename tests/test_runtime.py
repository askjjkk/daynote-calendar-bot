import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

from store import Store
from bot import Bot
from transport import Telegram, ApiError, drain
from main import load_settings, ProcessLock, Settings, run
from views import MOSCOW


class FakeApi:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def call(self, method, payload):
        self.calls.append((method, payload))
        if self.error:
            raise self.error
        return True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.s = Store(self.root / 'test.db')

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def queue(self):
        with self.s.db:
            self.s.enqueue('sendMessage', {'chat_id': 11, 'text': 'пример'})

    def test_retry_survives_reopen_then_success_clears_queue(self):
        self.queue()
        api = FakeApi(ApiError(503))
        drain(self.s, api, {11}, at=100)
        self.assertEqual(self.s.pending(100), [])
        self.assertEqual(len(self.s.pending(1000)), 1)
        self.s.close()
        self.s = Store(self.root / 'test.db')
        drain(self.s, FakeApi(), {11}, at=1000)
        self.assertEqual(self.s.pending(), [])

    def test_rate_limit_and_terminal_error(self):
        self.queue()
        drain(self.s, FakeApi(ApiError(429, retry_after=60)), {11}, at=100)
        self.assertEqual(self.s.pending(159), [])
        self.assertEqual(len(self.s.pending(161)), 1)
        drain(self.s, FakeApi(ApiError(403)), {11}, at=161)
        self.assertEqual(self.s.pending(), [])
        self.assertEqual(self.s.db.execute('SELECT error FROM outbox').fetchone()[0], 'Telegram 403')

    def test_removed_owner_receives_no_queued_personal_data(self):
        self.queue()
        api = FakeApi()
        drain(self.s, api, {22}, at=100)
        self.assertEqual(api.calls, [])
        self.assertEqual(self.s.pending(), [])

    def test_failed_reminder_not_marked_sent_and_success_is(self):
        with self.s.db:
            nid = self.s.add_note(11, '2026-11-07', '12:00 — Анна')
            self.s.alarm(11, nid, '2026-11-07T11:00:00+03:00')
        bot = Bot(self.s, {11})
        bot.queue_reminders(datetime(2026, 11, 7, 11, tzinfo=MOSCOW))
        drain(self.s, FakeApi(ApiError(503)), {11}, at=100)
        self.assertFalse(self.s.get_note(11, nid)['reminded'])
        drain(self.s, FakeApi(), {11}, at=1000)
        self.assertTrue(self.s.get_note(11, nid)['reminded'])

    def test_network_exception_does_not_expose_token(self):
        token = '123456:local_fake_token_value'
        api = Telegram(token)
        with patch('transport.urllib.request.urlopen', side_effect=OSError('URL contains ' + token)):
            with self.assertRaises(ApiError) as c:
                api.call('getMe', {})
        self.assertNotIn(token, str(c.exception))
        self.assertIsNone(c.exception.__cause__)

    def test_dotenv_validation_does_not_print_secrets(self):
        (self.root / '.env').write_text('BOT_TOKEN=\nALLOWED_USER_IDS=123456789\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                load_settings(self.root)
        (self.root / '.env').write_text('BOT_TOKEN=123456:abcdefghijk\nALLOWED_USER_IDS=11,22\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True):
            settings = load_settings(self.root)
            self.assertEqual(settings.allowed, {11, 22})
            self.assertEqual(settings.db_path, self.root / 'data' / 'calendar.sqlite3')
        (self.root / '.env').write_text('BOT_TOKEN=123456:abcdefghijk\nALLOWED_USER_IDS=0\n', encoding='utf-8')
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(ValueError):
            load_settings(self.root)

    def test_second_process_is_blocked_and_lock_releases(self):
        path = self.root / 'bot.lock'
        with ProcessLock(path):
            with self.assertRaises(RuntimeError):
                with ProcessLock(path):
                    pass
        with ProcessLock(path):
            pass

    def test_backup_reopens_with_notes(self):
        with self.s.db:
            self.s.add_note(11, '2026-11-07', 'сохранить')
        backup = self.root / 'backup.sqlite3'
        self.s.backup(backup)
        other = Store(backup)
        try:
            self.assertEqual(other.notes(11, '2026-11-07')[0]['text'], 'сохранить')
        finally:
            other.close()

    def test_transport_posts_unicode_json_and_reads_response(self):
        response = io.BytesIO(b'{"ok":true,"result":{"message_id":17}}')
        with patch('transport.urllib.request.urlopen', return_value=response) as request:
            result = Telegram('123456:fake_test_token').call('sendMessage', {'chat_id': 11, 'text': 'Анна'})
        self.assertEqual(result['message_id'], 17)
        req = request.call_args.args[0]
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(json.loads(req.data)['text'], 'Анна')
        self.assertEqual(req.get_header('Content-type'), 'application/json')

    def test_check_only_reads_and_never_deletes_webhook(self):
        settings = Settings('123456:fake_test_token', {11}, self.root / 'bot.db')
        with patch('main.Telegram') as api_class:
            api = api_class.return_value
            api.call.side_effect = [{'username': 'test_bot'}, {'url': ''}]
            run(settings, check=True)
            self.assertEqual([c.args[0] for c in api.call.call_args_list], ['getMe', 'getWebhookInfo'])
            self.assertFalse(settings.db_path.exists())
        with patch('main.Telegram') as api_class:
            api = api_class.return_value
            api.call.side_effect = [{'username': 'test_bot'}, {'url': 'https://example.org/hook'}]
            with self.assertRaises(ValueError):
                run(settings, check=True)
            self.assertNotIn('deleteWebhook', [c.args[0] for c in api.call.call_args_list])


if __name__ == '__main__':
    unittest.main()
