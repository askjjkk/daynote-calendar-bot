from test_bot import BotTests, NOW
from bot_v2 import Bot

class NewFlowTests(BotTests):
    def setUp(self):
        super().setUp()
        self.bot = Bot(self.s, {11,22})

    def test_new_creation_flow(self):
        self.update(text='/start')
        self.assertIn('Создать', str(self.s.pending()))
        self.update(data='create')
        self.update(text='Встреча')
        self.assertEqual(self.s.notes(11,'2026-11-07'), [])
        self.update(data='pick:2026-11-07')
        self.update(text='12:30')
        self.update(data='save')
        n=self.s.notes(11,'2026-11-07')[0]
        self.assertEqual(n['text'],'Встреча')
        self.assertEqual(n['event_time'],'12:30')

    def test_navigation_edits_existing_message(self):
        self.bot.handle({'update_id':1,'callback_query':{'id':'x','from':{'id':11},'data':'menu',
            'message':{'message_id':101,'chat':{'id':11,'type':'private'}}}},NOW)
        self.assertTrue(any(r['method']=='editMessageText' for r in self.s.pending()))

    def test_global_reminders_switch(self):
        self.update(data='pause')
        self.assertFalse(self.bot.enabled(11))
        self.update(data='resume')
        self.assertTrue(self.bot.enabled(11))
