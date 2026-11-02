from test_bot import BotTests, NOW
from bot_groups import Bot
from access import AccessPolicy

class PublicTests(BotTests):
    def setUp(self):
        super().setUp(); self.bot=Bot(self.s,{11,22}); self.bot.allowed=AccessPolicy({11,22},public=True)

    def test_new_user_can_create_without_allowlist(self):
        self.update(text='/start',owner=33)
        self.assertIn('Календарь записей',self.last_text())
        self.update(data='add:2026-11-07',owner=33); self.update(text='Моя запись',owner=33)
        self.assertEqual(len(self.s.notes(33,'2026-11-07')),1)

    def test_no_admin_access_and_notes_stay_private(self):
        n=self.note(owner=22)
        self.update(data=f'open:{n["id"]}',owner=11)
        self.assertNotIn(n['text'],self.last_text())
        self.assertIn('не найдена',self.last_text())
        self.update(data=f'edit:{n["id"]}',owner=33)
        self.assertIsNone(self.s.state(33))

    def test_public_policy_blocks_group_ids(self):
        self.assertIn(999,self.bot.allowed)
        self.assertNotIn(-999,self.bot.allowed)
        self.assertNotIn(0,self.bot.allowed)

    def test_reminders_discover_new_users_after_restart(self):
        n=self.note(owner=33)
        with self.s.db:
            self.s.alarm(33,n['id'],'2026-11-02T11:00:00+03:00')
        self.bot=Bot(self.s,set());self.bot.allowed=AccessPolicy(public=True)
        self.bot.queue_reminders(NOW)
        self.assertTrue(any(p['note_id']==n['id'] for p in self.s.pending()))
