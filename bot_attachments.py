"""Optional Telegram-hosted attachments. Store file IDs, never download private files."""
from bot_v2 import Bot as MenuBot
from views import message as m, button as b

KINDS={'photo':('sendPhoto','Фото'),'document':('sendDocument','Файл'),
       'video':('sendVideo','Видео'),'audio':('sendAudio','Аудио'),
       'voice':('sendVoice','Голосовое'),'animation':('sendAnimation','Анимация'),
       'sticker':('sendSticker','Стикер'),'video_note':('sendVideoNote','Видеосообщение')}


class Bot(MenuBot):
    def __init__(self,store,allowed):
        super().__init__(store,allowed)
        with store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,file_id TEXT NOT NULL,label TEXT NOT NULL)''')

    def attachments(self,owner,nid):
        return [dict(r) for r in self.s.db.execute('''SELECT a.* FROM attachments a
            JOIN notes n ON n.id=a.note_id WHERE n.owner=? AND n.id=? ORDER BY a.id''',(owner,nid))]

    def handle(self,update,now):
        msg=update.get('message',{})
        kind=next((k for k in KINDS if msg.get(k)),None)
        if not kind:
            return super().handle(update,now)
        owner=msg.get('from',{}).get('id'); chat=msg.get('chat',{})
        if owner not in self.allowed or chat.get('type')!='private' or chat.get('id')!=owner:
            return super().handle(update,now)
        with self.s.db:
            if update['update_id']<self.s.offset(): return
            self.s.set_offset(update['update_id']+1)
            state=self.s.state(owner)
            if not state or state['mode'] not in ('draft_date','draft_time','draft_manual','draft_confirm','attachments'):
                self.send(owner,m('📎 Сначала создайте заметку или откройте запись → «Прикрепить».',[[b('Главное меню','menu')]])); return
            media=msg[kind][-1] if kind=='photo' else msg[kind]
            item={'kind':kind,'file_id':media['file_id'],
                  'label':str(media.get('file_name') or media.get('title') or KINDS[kind][1])[:100]}
            if state['mode']=='attachments':
                n=self.note(owner,state['id'])
                if not n: self.s.set_state(owner); return
                if len(self.attachments(owner,n['id']))>=20:
                    self.send(owner,m('В одной заметке можно сохранить до 20 вложений.')); return
                self.persist(n['id'],item)
                rows=[[b('Готово',f'open:{n["id"]}')]]
            else:
                items=state.setdefault('files',[])
                if len(items)>=20:
                    self.send(owner,m('В одной заметке можно сохранить до 20 вложений.')); return
                items.append(item); self.s.set_state(owner,state)
                # Keep date/time/confirmation input available after optional media upload.
                rows=[[b('Продолжить','continue_draft')]]
            self.send(owner,m('📎 Вложение сохранено: '+item['label']+'\nМожно отправить ещё или продолжить.',rows))

    def persist(self,nid,item):
        self.s.db.execute('INSERT INTO attachments(note_id,kind,file_id,label) VALUES(?,?,?,?)',
            (nid,item['kind'],item['file_id'],item['label']))

    def after_save(self,owner,nid,state):
        for item in state.get('files',[]): self.persist(nid,item)

    def send(self,owner,view):
        text=view.get('text','')
        if text.startswith('🗓 Выберите месяц'):
            view=dict(view,text=text+'\n\n📎 По желанию отправьте фото, файл или другие медиа — они прикрепятся к заметке.')
        # Decorate both menu and legacy note cards without replacing their navigation logic.
        rows=view.get('reply_markup',{}).get('inline_keyboard',[])
        nid=None
        for row in rows:
            for btn in row:
                if btn.get('callback_data','').startswith('edit:'):
                    nid=int(btn['callback_data'].split(':')[1])
        if nid and self.s.get_note(owner,nid):
            files=self.attachments(owner,nid)
            extra=[[b('📎 Прикрепить',f'attach:{nid}')]]
            if files: extra.append([b(f'Вложения · {len(files)}',f'files:{nid}')])
            view=dict(view,text=text+f'\n\n📎 Вложений: {len(files)}',reply_markup={'inline_keyboard':rows+extra})
        super().send(owner,view)

    def callback(self,owner,data,now):
        p=data.split(':'); action=p[0]
        if action=='continue_draft':
            state=self.s.state(owner)
            if not state: self.menu(owner); return
            mode=state['mode']
            if mode=='draft_date': self.picker(owner,state,now.year,now.month)
            elif mode=='draft_time': self.send(owner,m('🕒 Напишите время ЧЧ:ММ, например 12:30.',[[b('Отмена','menu')]]))
            elif mode=='draft_manual': self.send(owner,m('⌨️ Напишите дату и время, например 31.12.2026 14:30.',[[b('Отмена','menu')]]))
            elif mode=='draft_confirm': self.send(owner,m('✅ Заметка и вложения готовы к сохранению.',[[b('Сохранить','save'),b('Отмена','menu')]]))
            else: self.menu(owner)
        elif action in ('attach','files'):
            n=self.note(owner,int(p[1]))
            if not n: return
            if action=='attach':
                self.s.set_state(owner,{'mode':'attachments','id':n['id']})
                self.send(owner,m('📎 Отправьте фото, файл, видео, аудио, голосовое, стикер или видеосообщение.\n'
                    'Можно несколько, до 20 вложений на заметку.\nКогда закончите — нажмите «Готово».',[[b('Готово',f'open:{n["id"]}')]]))
            else:
                files=self.attachments(owner,n['id'])
                rows=[[b(f'{i}. '+a['label'][:50],f'file:{a["id"]}')] for i,a in enumerate(files,1)]
                rows.append([b('← К записи',f'open:{n["id"]}')])
                self.send(owner,m('📎 Вложения\nНажмите, чтобы получить нужный файл.',rows))
        elif action=='file':
            a=self.s.db.execute('''SELECT a.* FROM attachments a JOIN notes n ON n.id=a.note_id
                WHERE a.id=? AND n.owner=?''',(int(p[1]),owner)).fetchone()
            if not a:
                self.send(owner,m('Вложение не найдено.')); return
            self.s.enqueue(KINDS[a['kind']][0],{'chat_id':owner,a['kind']:a['file_id']})
        else: super().callback(owner,data,now)
