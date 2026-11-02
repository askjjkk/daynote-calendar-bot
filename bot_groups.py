"""Private note folders; deleting a folder preserves its notes and attachments."""
from bot_attachments import Bot as AttachmentBot
from views import message as m, button as b, date_label
from help_text import HELP_TEXT


class Bot(AttachmentBot):
    def __init__(self,store,allowed):
        super().__init__(store,allowed)
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT,owner INTEGER NOT NULL,name TEXT NOT NULL)')
            if 'group_id' not in {r[1] for r in store.db.execute('PRAGMA table_info(notes)')}:
                store.db.execute('ALTER TABLE notes ADD COLUMN group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL')

    def group(self,owner,gid):
        return self.s.db.execute('SELECT * FROM groups WHERE owner=? AND id=?',(owner,gid)).fetchone()

    def send(self,owner,view):
        rows=view.get('reply_markup',{}).get('inline_keyboard',[])
        if view.get('text','').startswith('📓 Календарь записей'):
            view=dict(view,reply_markup={'inline_keyboard':rows+[[b('📂 Группы','groups:0')]]})
        else:
            nid=next((int(x['callback_data'].split(':')[1]) for row in rows for x in row if x.get('callback_data','').startswith('edit:')),None)
            if nid:
                n=self.s.get_note(owner,nid)
                if n:
                    g=self.group(owner,n['group_id']) if n.get('group_id') else None
                    view=dict(view,text=view['text']+'\n📂 '+(g['name'] if g else 'Без группы'),
                        reply_markup={'inline_keyboard':rows+[[b('📂 Изменить группу',f'choose_group:{nid}:0')]]})
        super().send(owner,view)

    def groups_view(self,owner,page=0,nid=None):
        groups=self.s.db.execute('SELECT * FROM groups WHERE owner=? ORDER BY name,id',(owner,)).fetchall()
        page=min(max(0,page),max(0,(len(groups)-1)//8)); rows=[]
        for g in groups[page*8:page*8+8]:
            route=f'assign:{nid}:{g["id"]}' if nid else f'group_open:{g["id"]}:0'
            rows.append([b('📂 '+g['name'],route)])
        nav=[]
        route=f'choose_group:{nid}' if nid else 'groups'
        if page: nav.append(b('‹',f'{route}:{page-1}'))
        if (page+1)*8<len(groups): nav.append(b('›',f'{route}:{page+1}'))
        if nav: rows.append(nav)
        rows.append([b('Без группы',f'assign:{nid}:0' if nid else 'group_open:0:0')])
        if not nid: rows.append([b('＋ Создать группу','group_new')])
        rows.append([b('Назад',f'open:{nid}' if nid else 'menu')])
        self.send(owner,m('📂 Выберите группу для заметки' if nid else '📂 Ваши группы\n\nОбъединяйте связанные заметки в папки.',rows))

    def after_save(self,owner,nid,state):
        super().after_save(owner,nid,state)
        # Folder selection follows saving: it is optional, and the default is ungrouped.

    def text(self,owner,text,now):
        if text.strip().split('@')[0]=='/help':
            self.s.set_state(owner)
            self.send(owner,m(HELP_TEXT,[[b('← Главное меню','menu')]])); return
        st=self.s.state(owner)
        if st and st['mode'] in ('group_name','group_rename') and not text.strip().startswith('/'):
            name=' '.join(text.split())
            if not name or len(name.encode('utf-16-le'))//2>50:
                self.send(owner,m('Название должно содержать от 1 до 50 символов.')); return
            if st['mode']=='group_name':
                self.s.db.execute('INSERT INTO groups(owner,name) VALUES(?,?)',(owner,name))
            elif self.group(owner,st['id']):
                self.s.db.execute('UPDATE groups SET name=? WHERE owner=? AND id=?',(name,owner,st['id']))
            self.s.set_state(owner); self.groups_view(owner)
        else: super().text(owner,text,now)

    def callback(self,owner,data,now):
        p=data.split(':'); action=p[0]
        if action in ('guide','help'):
            self.s.set_state(owner)
            self.send(owner,m(HELP_TEXT,[[b('← Главное меню','menu')]]))
        elif action=='groups':
            self.s.set_state(owner); self.groups_view(owner,int(p[1]) if len(p)>1 else 0)
        elif action=='group_new':
            self.s.set_state(owner,{'mode':'group_name'})
            self.send(owner,m('📂 Как назвать группу?\nНапишите название, например «Работа» или «Личное».',[[b('Отмена','groups:0')]]))
        elif action=='choose_group':
            if self.note(owner,int(p[1])): self.groups_view(owner,int(p[2]),int(p[1]))
        elif action=='assign':
            nid,gid=map(int,p[1:3]); n=self.note(owner,nid)
            if not n: return
            if gid and not self.group(owner,gid):
                self.send(owner,m('Группа не найдена.')); return
            self.s.db.execute('UPDATE notes SET group_id=? WHERE owner=? AND id=?',(gid or None,owner,nid))
            self.s.set_state(owner); self.card(owner,self.s.get_note(owner,nid))
        elif action in ('group_open','group_rename','group_delete','group_confirm'):
            gid=int(p[1]); g=self.group(owner,gid) if gid else None
            if not g and not (action=='group_open' and gid==0):
                self.send(owner,m('Группа не найдена.',[[b('Группы','groups:0')]])); return
            if action=='group_rename':
                self.s.set_state(owner,{'mode':'group_rename','id':gid})
                self.send(owner,m('✏️ Текущее название: '+g['name']+'\n\nНапишите новое название.',[[b('Отмена',f'group_open:{gid}:0')]]))
            elif action=='group_delete':
                self.s.set_state(owner,{'mode':'group_delete','id':gid})
                self.send(owner,m('Удалить группу «'+g['name']+'»?\n\nВсе заметки и вложения сохранятся в «Без группы».',
                    [[b('Да, удалить группу',f'group_confirm:{gid}'),b('Отмена',f'group_open:{gid}:0')]]))
            elif action=='group_confirm':
                if self.s.state(owner)!={'mode':'group_delete','id':gid}:
                    self.send(owner,m('Подтверждение устарело.',[[b('Группы','groups:0')]])); return
                self.s.db.execute('UPDATE notes SET group_id=NULL WHERE owner=? AND group_id=?',(owner,gid))
                self.s.db.execute('DELETE FROM groups WHERE owner=? AND id=?',(owner,gid))
                self.s.set_state(owner); self.groups_view(owner)
            else:
                self.s.set_state(owner)
                page=max(0,int(p[2]))
                notes=[dict(r) for r in self.s.db.execute('SELECT * FROM notes WHERE owner=? AND group_id IS ? ORDER BY day,event_time,id',(owner,gid or None))]
                page=min(page,max(0,(len(notes)-1)//5)); lines=['📂 '+(g['name'] if g else 'Без группы'),'']; rows=[]
                for n in notes[page*5:page*5+5]:
                    label=date_label(n['day'])+' · '+(n['event_time'] or 'без времени')
                    lines.extend([label,n['text'][:350],'']); rows.append([b('📌 '+label,f'open:{n["id"]}')])
                if not notes: lines.append('В этой группе пока нет заметок.')
                nav=[]
                if page: nav.append(b('‹',f'group_open:{gid}:{page-1}'))
                if (page+1)*5<len(notes): nav.append(b('›',f'group_open:{gid}:{page+1}'))
                if nav: rows.append(nav)
                if gid: rows.extend([[b('✏️ Название',f'group_rename:{gid}'),b('Удалить группу',f'group_delete:{gid}')]])
                rows.extend([[b('＋ Создать заметку','create')],[b('← Группы','groups:0')]])
                self.send(owner,m('\n'.join(lines),rows))
        else: super().callback(owner,data,now)
