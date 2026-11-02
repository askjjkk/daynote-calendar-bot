from test_bot import BotTests, NOW
from bot_attachments import Bot

class AttachmentTests(BotTests):
    def setUp(self):
        super().setUp()
        self.bot=Bot(self.s,{11,22})

    def media(self,owner=11,**fields):
        self.uid+=1
        u={'update_id':self.uid,'message':{'chat':{'id':owner,'type':'private'},'from':{'id':owner},**fields}}
        self.bot.handle(u,NOW)
        return u

    def test_attach_to_existing_and_private_delivery(self):
        n=self.note()
        self.update(data=f'attach:{n["id"]}')
        u=self.media(photo=[{'file_id':'small'},{'file_id':'large'}])
        self.bot.handle(u,NOW)
        rows=self.s.db.execute('SELECT * FROM attachments').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['file_id'],'large')
        count=len(self.s.pending())
        self.update(data=f'file:{rows[0]["id"]}',owner=22)
        self.assertFalse(any(x['method']=='sendPhoto' for x in self.s.pending()[count:]))
        self.update(data=f'file:{rows[0]["id"]}')
        self.assertTrue(any(x['method']=='sendPhoto' and x['payload']['photo']=='large' for x in self.s.pending()))

    def test_draft_file_survives_and_saves_with_note(self):
        self.update(data='create'); self.update(text='Документы')
        self.media(document={'file_id':'pdf_id','file_name':'plan.pdf'})
        self.update(data='pick:2026-11-07'); self.update(text='12:00'); self.update(data='save')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0],1)
        n=self.s.notes(11,'2026-11-07')[0]
        self.update(data=f'delete:{n["id"]}'); self.update(data=f'confirm:{n["id"]}')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0],0)

    def test_direct_datetime_and_full_calendar_title(self):
        self.update(data='create'); self.update(text='Встреча')
        view=[x['payload'] for x in self.s.pending() if x['method']=='sendMessage'][-1]
        self.assertIn('Ноябрь 2026',view['text'])
        self.assertEqual(len(view['reply_markup']['inline_keyboard'][0]),2)
        self.update(data='manual_date')
        self.update(text='31.12.2026 14:30')
        self.update(data='save')
        n=self.s.notes(11,'2026-12-31')[0]
        self.assertEqual(n['event_time'],'14:30')

    def test_invalid_direct_datetime_keeps_draft(self):
        self.update(data='create'); self.update(text='План')
        self.update(data='manual_date'); self.update(text='31.02.2026 14:30')
        self.assertEqual(self.s.state(11)['mode'],'draft_manual')
