from test_bot import BotTests
from bot_groups import Bot

class GroupTests(BotTests):
    def setUp(self):
        super().setUp(); self.bot=Bot(self.s,{11,22})

    def test_create_assign_rename_delete_preserves_note(self):
        n=self.note()
        self.update(data='group_new'); self.update(text='Работа')
        g=self.s.db.execute('SELECT id FROM groups').fetchone()[0]
        self.update(data=f'assign:{n["id"]}:{g}')
        self.assertEqual(self.s.get_note(11,n['id'])['group_id'],g)
        self.update(data=f'group_rename:{g}'); self.update(text='Дела')
        self.assertEqual(self.s.db.execute('SELECT name FROM groups').fetchone()[0],'Дела')
        self.update(data=f'group_delete:{g}'); self.update(data=f'group_confirm:{g}')
        self.assertIsNone(self.s.get_note(11,n['id'])['group_id'])

    def test_other_owner_cannot_assign_or_rename(self):
        n=self.note()
        self.update(data='group_new'); self.update(text='Личное')
        g=self.s.db.execute('SELECT id FROM groups').fetchone()[0]
        self.update(data=f'group_rename:{g}',owner=22); self.update(text='Чужое',owner=22)
        self.assertEqual(self.s.db.execute('SELECT name FROM groups').fetchone()[0],'Личное')
        self.update(data=f'assign:{n["id"]}:{g}',owner=22)
        self.assertIsNone(self.s.get_note(11,n['id'])['group_id'])
