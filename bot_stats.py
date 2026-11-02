"""Aggregate usage statistics only. No access to other people's note content."""
from datetime import timedelta
from bot_groups import Bot as GroupBot
from views import message,button,MOSCOW


class Bot(GroupBot):
    def __init__(self,store,allowed,admins=()):
        super().__init__(store,allowed)
        self.allowed=allowed
        self.admins=set(admins)
        with store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS usage_stats (
                owner INTEGER PRIMARY KEY,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
                updates INTEGER NOT NULL DEFAULT 0)''')

    def stats(self,owner,now):
        if owner not in self.admins:
            self.send(owner,message('Эта команда недоступна.',[[button('Главное меню','menu')]]));return
        values=[]
        total=self.s.db.execute('SELECT COUNT(*) FROM usage_stats').fetchone()[0]
        for days in (1,7,30):
            since=(now.astimezone(MOSCOW)-timedelta(days=days)).isoformat(timespec='seconds')
            values.append(self.s.db.execute('SELECT COUNT(*) FROM usage_stats WHERE last_seen>=?',(since,)).fetchone()[0])
        self.send(owner,message(f'📊 Статистика бота\n\nВсего пользователей: {total}\n'
            f'Активны за 24 часа: {values[0]}\nЗа 7 дней: {values[1]}\nЗа 30 дней: {values[2]}\n\n'
            'Учитываются пользователи, которые отправляли сообщения или нажимали кнопки. '
            'Статистика собирается с момента обновления. Текст заметок здесь не отображается.',
            [[button('↻ Обновить','stats')],[button('Главное меню','menu')]]))

    def send(self,owner,view):
        if owner in self.admins and view.get('text','').startswith('📓 Календарь записей'):
            rows=view.get('reply_markup',{}).get('inline_keyboard',[])
            view=dict(view,reply_markup={'inline_keyboard':rows+[[button('📊 Статистика','stats')]]})
        super().send(owner,view)

    def handle(self,update,now):
        cb=update.get('callback_query');msg=cb.get('message',{}) if cb else update.get('message',{})
        owner=(cb or msg).get('from',{}).get('id');chat=msg.get('chat',{})
        valid=owner in self.allowed and chat.get('type')=='private' and chat.get('id')==owner
        if valid and update.get('update_id',-1)>=self.s.offset():
            with self.s.db:
                timestamp=now.astimezone(MOSCOW).isoformat(timespec='seconds')
                self.s.db.execute('''INSERT INTO usage_stats VALUES(?,?,?,1)
                    ON CONFLICT(owner) DO UPDATE SET last_seen=excluded.last_seen,updates=usage_stats.updates+1''',
                    (owner,timestamp,timestamp))
        super().handle(update,now)

    def text(self,owner,text,now):
        if text.strip().split('@')[0] in ('/admin','/stats'):
            self.stats(owner,now)
        else:super().text(owner,text,now)

    def callback(self,owner,data,now):
        if data=='stats':self.stats(owner,now)
        else:super().callback(owner,data,now)
