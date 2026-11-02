import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from store import Store
from views import calendar_view, parse_day, parse_alarm, MOSCOW
from bot import Bot


NOW = datetime(2026, 11, 2, 12, tzinfo=MOSCOW)


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.s = Store(self.path)
        self.bot = Bot(self.s, {11, 22})
        self.uid = 0

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def update(self, text=None, data=None, owner=11, chat=None, kind='private', now=NOW):
        self.uid += 1
        msg = {'chat': {'id': owner if chat is None else chat, 'type': kind}, 'from': {'id': owner}}
        u = {'update_id': self.uid}
        if data is None:
            u['message'] = dict(msg, text=text)
        else:
            u['callback_query'] = {'id': str(self.uid), 'from': {'id': owner},
                                   'message': msg, 'data': data}
        self.bot.handle(u, now)
        return u

    def note(self, text='12:00 — Анна', day='2026-11-07', owner=11):
        self.update(data='add:' + day, owner=owner)
        self.update(text=text, owner=owner)
        return self.s.notes(owner, day)[-1]

    def last_text(self):
        return [r['payload']['text'] for r in self.s.pending() if r['method'] == 'sendMessage'][-1]

    def test_calendar_november_and_month_marks(self):
        v = calendar_view(2026, 11, {'2026-11-07'}, date(2026, 11, 2))
        rows = v['reply_markup']['inline_keyboard']
        self.assertEqual([x['text'] for x in rows[2]], [' ', ' ', ' ', ' ', ' ', ' ', '1'])
        self.assertEqual(rows[3][5]['callback_data'], 'day:2026-11-07:0')
        self.assertIn('•', rows[3][5]['text'])
        self.assertLessEqual(max(len(b.get('callback_data', '').encode()) for r in rows for b in r), 64)

    def test_date_validation_and_leap_year(self):
        self.assertEqual(parse_day('29.02.2028'), date(2028, 2, 29))
        for bad in ['29.02.2027', '31.11.2026', 'hello', '1/2/26']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_day(bad)
        self.assertEqual(parse_alarm('07.11.2026 11:00', NOW).hour, 11)
        for bad in ['01.11.2026 11:00', '07.11.2026 25:00']:
            with self.assertRaises(ValueError):
                parse_alarm(bad, NOW)

    def test_add_free_text_and_reopen_database(self):
        n = self.note('Время пока не выбрано\n<эскиз> & детали')
        self.s.close()
        self.s = Store(self.path)
        self.assertEqual(self.s.get_note(11, n['id'])['text'], n['text'])
        self.assertIsNone(self.s.get_note(22, n['id']))

    def test_pending_input_survives_restart_and_cancel(self):
        self.update(data='add:2026-11-07')
        self.s.close()
        self.s = Store(self.path)
        self.bot = Bot(self.s, {11, 22})
        self.update(text='16:30 — Мария')
        self.assertEqual(len(self.s.notes(11, '2026-11-07')), 1)
        self.update(data='add:2026-11-07')
        self.update(text='/cancel')
        self.update(text='не сохранять')
        self.assertEqual(len(self.s.notes(11, '2026-11-07')), 1)

    def test_edit_delete_confirmation_and_cancel(self):
        n = self.note()
        self.update(data=f'edit:{n["id"]}')
        self.update(text='13:00 — Анна, новый эскиз')
        self.assertIn('13:00', self.s.get_note(11, n['id'])['text'])
        self.update(data=f'delete:{n["id"]}')
        self.assertIsNotNone(self.s.get_note(11, n['id']))
        self.update(data=f'note:{n["id"]}')
        self.assertIsNotNone(self.s.get_note(11, n['id']))
        self.update(data=f'confirm:{n["id"]}')
        self.assertIsNotNone(self.s.get_note(11, n['id']))
        self.update(data=f'delete:{n["id"]}')
        self.update(data=f'confirm:{n["id"]}')
        self.assertIsNone(self.s.get_note(11, n['id']))
        self.update(data=f'edit:{n["id"]}')
        self.assertIn('не найдена', self.last_text())

    def test_unauthorized_and_group_access_do_not_touch_data(self):
        n = self.note()
        for owner, chat, kind in [(33, 33, 'private'), (11, -100, 'group'), (11, 22, 'private')]:
            self.update(data=f'confirm:{n["id"]}', owner=owner, chat=chat, kind=kind)
        self.update(data=f'confirm:{n["id"]}', owner=22)
        self.assertIsNotNone(self.s.get_note(11, n['id']))
        self.assertIn('не найдена', self.last_text())

    def test_update_is_idempotent_and_replies_are_durable(self):
        self.update(data='add:2026-11-07')
        u = self.update(text='одна запись')
        count = len(self.s.pending())
        self.bot.handle(u, NOW)
        self.assertEqual(len(self.s.notes(11, '2026-11-07')), 1)
        self.assertEqual(len(self.s.pending()), count)
        self.assertEqual(self.s.offset(), u['update_id'] + 1)

    def test_multiple_pages_and_long_text_limits(self):
        for i in range(10):
            self.note(f'{i}: запись')
        self.update(data='day:2026-11-07:1')
        self.assertIn('Страница 2', self.last_text())
        self.update(data='upcoming:1')
        self.assertIn('Страница 2', self.last_text())
        self.update(data='add:2026-11-07')
        self.update(text='😀' * 1600)
        self.assertEqual(len(self.s.notes(11, '2026-11-07')), 10)
        self.update(text='😀' * 1400)
        self.assertEqual(len(self.s.notes(11, '2026-11-07')), 11)
        for p in self.s.pending():
            if p['method'] == 'sendMessage':
                self.assertLessEqual(len(p['payload']['text'].encode('utf-16-le')) // 2, 4096)

    def test_alarm_future_validation_disable_and_restart(self):
        n = self.note()
        self.update(data=f'alarm:{n["id"]}')
        self.update(text='01.11.2026 11:00')
        self.assertIsNone(self.s.get_note(11, n['id'])['remind_at'])
        self.update(text='07.11.2026 11:00')
        self.assertEqual(self.s.get_note(11, n['id'])['remind_at'], '2026-11-07T11:00:00+03:00')
        self.assertEqual(self.s.due(NOW), [])
        self.s.close()
        self.s = Store(self.path)
        self.bot = Bot(self.s, {11, 22})
        later = NOW + timedelta(days=6)
        self.assertEqual(len(self.s.due(later)), 1)
        self.bot.queue_reminders(later)
        self.bot.queue_reminders(later)
        alarms = [p for p in self.s.pending() if p['note_id'] == n['id']]
        self.assertEqual(len(alarms), 1)
        self.assertIn('пропущено', alarms[0]['payload']['text'])
        self.update(data=f'off:{n["id"]}')
        self.assertEqual([p for p in self.s.pending() if p['note_id'] == n['id']], [])

    def test_queued_reminder_uses_updated_text_and_delete_cancels_it(self):
        n = self.note()
        self.update(data=f'alarm:{n["id"]}')
        self.update(text='07.11.2026 11:00')
        self.bot.queue_reminders(NOW + timedelta(days=6))
        self.update(data=f'edit:{n["id"]}')
        self.update(text='новый текст')
        self.assertIn('новый текст', [p for p in self.s.pending() if p['note_id'] == n['id']][0]['payload']['text'])
        self.update(data=f'delete:{n["id"]}')
        self.update(data=f'confirm:{n["id"]}')
        self.assertFalse([p for p in self.s.pending() if p['note_id'] == n['id']])

    def test_deleted_note_id_is_not_reused_by_old_button(self):
        n = self.note('первая')
        self.update(data=f'delete:{n["id"]}')
        self.update(data=f'confirm:{n["id"]}')
        second = self.note('вторая')
        self.assertNotEqual(n['id'], second['id'])
        self.update(data=f'confirm:{n["id"]}')
        self.assertIsNotNone(self.s.get_note(11, second['id']))

    def test_bad_buttons_and_empty_input_do_not_crash(self):
        for data in ['cal:99999:77', 'day:no:0', 'note:no', 'upcoming:-2', 'nonsense']:
            self.update(data=data)
        self.update(data='add:2026-11-07')
        self.update(text='  ')
        self.assertEqual(self.s.notes(11, '2026-11-07'), [])
        self.update(text='/start')
        self.assertIsNone(self.s.state(11))


if __name__ == '__main__':
    unittest.main()
