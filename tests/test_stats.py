from test_bot import BotTests,NOW
from bot_stats import Bot
from access import AccessPolicy

class StatsTests(BotTests):
    def setUp(self):
        super().setUp();self.bot=Bot(self.s,AccessPolicy(public=True),admins={11})

    def test_public_stats_are_admin_only(self):
        self.update(text='/start',owner=33)
        self.update(text='/admin',owner=11)
        self.assertIn('Всего пользователей: 2',self.last_text())
        self.update(text='/admin',owner=33)
        self.assertIn('недоступна',self.last_text())

    def test_stats_do_not_show_note_content_or_group_chat(self):
        self.note('Частное содержимое',owner=33)
        self.update(text='/start',owner=44,chat=-100,kind='group')
        self.update(text='/admin',owner=11)
        self.assertNotIn('Частное содержимое',self.last_text())
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM usage_stats WHERE owner=44').fetchone()[0],0)
