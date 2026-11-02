"""Menu-driven calendar. Legacy handlers remain available for older chat buttons."""
from datetime import datetime, date, timedelta
import re
from bot import Bot as LegacyBot
from views import button as b, message as m, calendar_view, date_label, MOSCOW, parse_alarm


class Bot(LegacyBot):
    def __init__(self, store, allowed):
        super().__init__(store, allowed)
        columns={r[1] for r in store.db.execute('PRAGMA table_info(notes)')}
        if 'event_time' not in columns:
            with store.db:
                store.db.execute('ALTER TABLE notes ADD COLUMN event_time TEXT')
        self.panel=None

    def enabled(self, owner):
        r=self.s.db.execute('SELECT value FROM meta WHERE key=?',(f'enabled:{owner}',)).fetchone()
        return not r or r[0]=='1'

    def handle(self, update, now):
        self.panel=update.get('callback_query',{}).get('message',{}).get('message_id')
        try:
            super().handle(update, now)
        finally:
            self.panel=None

    def send(self, owner, view):
        if self.panel:
            self.s.enqueue('editMessageText',dict(view,chat_id=owner,message_id=self.panel))
        else:
            super().send(owner,view)

    def menu(self, owner):
        on=self.enabled(owner)
        self.send(owner,m('📓 Календарь записей\n\nВаши планы — в одном месте.\nСоздайте заметку или откройте текущие записи.\n\n'
            +('🔔 Напоминания включены' if on else '🔕 Напоминания на паузе')+' · Москва',[ 
            [b('＋ Создать заметку','create')], [b('📋 Текущие записи','list:0')],
            [b('🔕 Выключить напоминания','pause') if on else b('🔔 Включить напоминания','resume')],
            [b('Как пользоваться','guide')]]))

    def card(self,owner,n):
        alarm='выключено'
        if n['remind_at']:
            alarm=datetime.fromisoformat(n['remind_at']).strftime('%d.%m.%Y %H:%M')+' МСК'
        self.send(owner,m(f'📌 {date_label(n["day"])} · {n.get("event_time") or "время не задано"}\n\n{n["text"]}\n\n🔔 {alarm}',[
            [b('✏️ Текст',f'edit:{n["id"]}'), b('🗓 Дата и время',f'reschedule:{n["id"]}')],
            [b('🔔 Напоминание',f'remind:{n["id"]}')],
            [b('🗑 Удалить',f'delete:{n["id"]}')], [b('← К записям','list:0'),b('Главное меню','menu')]]))

    def picker(self,owner,state,year,month):
        v=calendar_view(year,month,set(),date.today())
        rows=v['reply_markup']['inline_keyboard'][:-2]
        month_title=rows[0][1]['text']
        rows[0]=[rows[0][0],rows[0][2]]
        for row in rows:
            for item in row:
                data=item['callback_data']
                if data.startswith('cal:'): item['callback_data']=data.replace('cal:','month:',1)
                elif data.startswith('day:'): item['callback_data']='pick:'+data.split(':')[1]
                elif data=='jump': item['callback_data']='noop'
        rows.append([b('⌨️ Ввести дату и время','manual_date')])
        rows.append([b('Отмена','menu')])
        self.send(owner,m('🗓 Выберите месяц стрелками, затем число.\n'+month_title+'\n\n'+state['text'][:180],rows))

    def callback(self,owner,data,now):
        p=data.split(':'); action=p[0]; state=self.s.state(owner)
        if action in ('menu','home','cancel'):
            self.s.set_state(owner); self.menu(owner)
        elif action=='guide':
            self.send(owner,m('📖 Как пользоваться\n\nСоздать заметку → текст → месяц и число → время → сохранить.\n'
                'Текущие записи → карточка → правка, удаление или напоминание.\n'
                'Общий выключатель ставит уведомления на паузу. После включения старые уведомления не отправляются.\n'
                'Все даты и время — Москва. /cancel — отмена.',[[b('← Главное меню','menu')]]))
        elif action in ('pause','resume'):
            self.s.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(f'enabled:{owner}','0' if action=='pause' else '1'))
            self.s.db.execute('DELETE FROM outbox WHERE note_id IN (SELECT id FROM notes WHERE owner=?)',(owner,))
            if action=='resume':
                self.s.db.execute('UPDATE notes SET reminded=1 WHERE owner=? AND remind_at<=?',(owner,now.isoformat(timespec='seconds')))
            self.menu(owner)
        elif action=='create':
            self.s.set_state(owner,{'mode':'draft_text'})
            self.send(owner,m('✍️ О чём напомнить?\n\nНапишите текст заметки. Дату и время выберем следующим шагом.',[[b('Отмена','menu')]]))
        elif action=='manual_date' and state and state['mode'] in ('draft_date','draft_manual'):
            state['mode']='draft_manual'; self.s.set_state(owner,state)
            self.send(owner,m('⌨️ Напишите дату и время одним сообщением:\n\n31.12.2026 14:30\n\nЧасовой пояс — Москва.',
                [[b('🗓 Выбрать в календаре','back_calendar'),b('Отмена','menu')]]))
        elif action=='back_calendar' and state and state['mode']=='draft_manual':
            state['mode']='draft_date'; self.s.set_state(owner,state); self.picker(owner,state,now.year,now.month)
        elif action=='month' and state and state['mode']=='draft_date':
            self.picker(owner,state,int(p[1]),int(p[2]))
        elif action=='pick' and state and state['mode']=='draft_date':
            date.fromisoformat(p[1]); state.update(day=p[1],mode='draft_time'); self.s.set_state(owner,state)
            self.send(owner,m(f'🕒 {date_label(p[1])}\n\nНапишите время: ЧЧ:ММ, например 12:30.\nЧасовой пояс — Москва.',[[b('Отмена','menu')]]))
        elif action=='save' and state and state['mode']=='draft_confirm':
            at=datetime.fromisoformat(state['day']+'T'+state['time']).replace(tzinfo=MOSCOW)
            if at<=now:
                self.send(owner,m('Это время уже прошло. Выберите новую дату и время.',[[b('Начать заново','create')]])); return
            if state.get('id'):
                n=self.note(owner,state['id'])
                if not n: return
                nid=n['id']
                self.s.db.execute('UPDATE notes SET day=?,event_time=? WHERE owner=? AND id=?',(state['day'],state['time'],owner,nid))
                self.s.alarm(owner,nid,None)
            else:
                nid=self.s.add_note(owner,state['day'],state['text'])
                self.s.db.execute('UPDATE notes SET event_time=? WHERE id=?',(state['time'],nid))
            self.after_save(owner,nid,state)
            self.s.set_state(owner); self.card(owner,self.s.get_note(owner,nid))
        elif action=='list':
            self.s.set_state(owner)
            page=max(0,int(p[1])); notes=self.s.upcoming(owner,now.date().isoformat())
            notes.sort(key=lambda n:(n['day'],n.get('event_time') or '99:99',n['id']))
            notes=[n for n in notes if not n.get('event_time') or n['day']+'T'+n['event_time']>=now.strftime('%Y-%m-%dT%H:%M')]
            page=min(page,max(0,(len(notes)-1)//5)); rows=[]; lines=['📋 Текущие записи','']
            for n in notes[page*5:page*5+5]:
                label=f'{date_label(n["day"])} · {n.get("event_time") or "без времени"}'
                lines.extend([label,n['text'][:350],''])
                rows.append([b('📌 '+label,f'open:{n["id"]}')])
            if not notes: lines.append('Пока нет записей. Создайте первую!')
            nav=[]
            if page: nav.append(b('‹ Назад',f'list:{page-1}'))
            if (page+1)*5<len(notes): nav.append(b('Далее ›',f'list:{page+1}'))
            if nav: rows.append(nav)
            rows.extend([[b('＋ Создать заметку','create')],[b('← Главное меню','menu')]])
            self.send(owner,m('\n'.join(lines),rows))
        elif action in ('open','note','reschedule','remind','preset'):
            n=self.note(owner,int(p[1]))
            if not n: return
            self.s.set_state(owner)
            if action in ('open','note'): self.card(owner,n)
            elif action=='reschedule':
                st={'mode':'draft_date','id':n['id'],'text':n['text']}; self.s.set_state(owner,st)
                self.picker(owner,st,int(n['day'][:4]),int(n['day'][5:7]))
            elif action=='remind':
                self.send(owner,m('🔔 Когда напомнить?\n\nОтсчёт от даты и времени записи.\nМожно задать своё точное время.',[
                    [b('За 15 минут',f'preset:{n["id"]}:15'),b('За час',f'preset:{n["id"]}:60')],
                    [b('За день',f'preset:{n["id"]}:1440'),b('В момент записи',f'preset:{n["id"]}:0')],
                    [b('Своё время',f'alarm:{n["id"]}'),b('Выключить',f'off:{n["id"]}')],
                    [b('← К записи',f'open:{n["id"]}')]]))
            else:
                if not n.get('event_time'):
                    self.send(owner,m('Сначала задайте дату и время записи.',[[b('Дата и время',f'reschedule:{n["id"]}')]])); return
                at=datetime.fromisoformat(n['day']+'T'+n['event_time']).replace(tzinfo=MOSCOW)-timedelta(minutes=int(p[2]))
                if at<=now:
                    self.send(owner,m('Это время уже прошло. Выберите другой вариант.',[[b('Назад',f'remind:{n["id"]}')]])); return
                self.s.alarm(owner,n['id'],at.isoformat(timespec='seconds')); self.card(owner,self.s.get_note(owner,n['id']))
        else:
            super().callback(owner,data,now)

    def text(self,owner,text,now):
        text=text.strip(); st=self.s.state(owner)
        if text.startswith('/'):
            self.s.set_state(owner)
            if text.split()[0] in ('/upcoming','/today'): self.callback(owner,'list:0',now)
            else: self.menu(owner)
            return
        if st and st['mode']=='draft_text':
            if not text or len(text.encode('utf-16-le'))//2>3000:
                self.send(owner,m('Введите текст от 1 до 3000 символов.')); return
            st={'mode':'draft_date','text':text}; self.s.set_state(owner,st); self.picker(owner,st,now.year,now.month)
        elif st and st['mode']=='draft_manual':
            try:
                at=parse_alarm(text,now)
            except ValueError as e:
                self.send(owner,m(str(e))); return
            st.update(day=at.date().isoformat(),time=at.strftime('%H:%M'),mode='draft_confirm'); self.s.set_state(owner,st)
            self.send(owner,m(f'✅ Проверьте запись\n\n{date_label(st["day"])} · {st["time"]}\n\n{st["text"]}',
                [[b('Сохранить','save'),b('Отмена','menu')]]))
        elif st and st['mode']=='draft_time':
            if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',text):
                self.send(owner,m('Нужно время в формате ЧЧ:ММ, например 12:30.')); return
            st.update(time=text,mode='draft_confirm'); self.s.set_state(owner,st)
            self.send(owner,m(f'✅ Проверьте запись\n\n{date_label(st["day"])} · {text}\n\n{st["text"]}\n\n'
                'Напоминание можно настроить после сохранения. При переносе старое напоминание выключится.',
                [[b('Сохранить','save'),b('Отмена','menu')]]))
        elif st:
            super().text(owner,text,now)
        else:
            self.menu(owner)

    def queue_reminders(self,now):
        original=self.allowed
        self.allowed={owner for owner in original if self.enabled(owner)}
        try: super().queue_reminders(now)
        finally: self.allowed=original

    def after_save(self,owner,nid,state):
        pass
