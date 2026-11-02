"""Telegram-independent conversation engine; all effects go through the outbox."""
import json
from datetime import date

from views import (MOSCOW, button, calendar_view, date_label, message, note_view,
                   parse_alarm, parse_day, reminder_view)

HELP = ('Это ваш личный календарь записей.\n\n'
        '1. Выберите день в календаре.\n2. Нажмите «Добавить заметку» и напишите текст. '
        'Время можно написать прямо в заметке.\n3. При желании настройте отдельное напоминание.\n\n'
        'Несколько записей в день разрешены. Внутри дня они идут по порядку добавления; '
        'время из текста не распознаётся автоматически.\n'
        'Все даты напоминаний — по Москве.\n\n'
        '/calendar — календарь\n/today — сегодня\n/upcoming — ближайшие записи\n'
        '/cancel — отменить ввод\n/help — помощь')


class Bot:
    def __init__(self, store, allowed):
        self.s = store
        self.allowed = set(allowed)

    def send(self, owner, view):
        self.s.enqueue('sendMessage', dict(view, chat_id=owner))

    def calendar(self, owner, year, month, now):
        self.send(owner, calendar_view(year, month, self.s.marked_days(owner, year, month), now.date()))

    def note(self, owner, note_id):
        n = self.s.get_note(owner, note_id)
        if not n:
            self.send(owner, message('Запись не найдена. Возможно, она уже удалена.', [[button('Календарь', 'home')]]))
        return n

    def listing(self, owner, day, page, now):
        if page < 0:
            raise ValueError('Неверная страница.')
        if day:
            date.fromisoformat(day)
            notes = self.s.notes(owner, day)
            title = 'Записи на ' + date_label(day)
            route = f'day:{day}'
        else:
            notes = self.s.upcoming(owner, now.date().isoformat())
            title = 'Ближайшие записи (начиная с сегодня)'
            route = 'upcoming'
        pages = max(1, (len(notes) + 7) // 8)
        page = min(page, pages - 1)
        visible = notes[page * 8:page * 8 + 8]
        rows = []
        lines = [title, f'Страница {page + 1} из {pages}', '']
        if not notes:
            lines.append('Заметок пока нет.')
        for i, n in enumerate(visible, page * 8 + 1):
            short = ' '.join(n['text'].split())[:110]
            prefix = f'{n["day"][8:10]}.{n["day"][5:7]} · ' if not day else ''
            lines.append(f'{i}. {prefix}{short}')
            rows.append([button(f'Открыть {i} · ' + short[:30], f'note:{n["id"]}')])
        nav = []
        if page:
            nav.append(button('‹ Назад', f'{route}:{page - 1}'))
        if page + 1 < pages:
            nav.append(button('Далее ›', f'{route}:{page + 1}'))
        if nav:
            rows.append(nav)
        if day:
            rows.append([button('＋ Добавить заметку', f'add:{day}')])
        rows.append([button('← Календарь', 'home')])
        self.send(owner, message('\n'.join(lines), rows))

    def handle(self, update, now):
        now = now.astimezone(MOSCOW)
        with self.s.db:
            uid = update['update_id']
            if uid < self.s.offset():
                return
            self.s.set_offset(uid + 1)
            cb = update.get('callback_query')
            msg = cb.get('message', {}) if cb else update.get('message', {})
            owner = (cb or msg).get('from', {}).get('id')
            chat = msg.get('chat', {})
            authorized = owner in self.allowed and chat.get('type') == 'private' and chat.get('id') == owner
            if cb:
                self.s.enqueue('answerCallbackQuery', {'callback_query_id': cb['id'],
                    'text': '' if authorized else 'Доступ закрыт.', 'show_alert': not authorized})
            if not authorized:
                if not cb and chat.get('type') == 'private' and chat.get('id') == owner:
                    self.send(owner, message('Это личный бот. Доступ закрыт.'))
                return
            try:
                if cb:
                    self.callback(owner, cb.get('data', ''), now)
                else:
                    self.text(owner, msg.get('text') or '', now)
            except (ValueError, OverflowError):
                self.send(owner, message('Эта кнопка устарела или дата неверна. Откройте календарь заново.',
                                         [[button('Календарь', 'home')]]))

    def callback(self, owner, data, now):
        parts = data.split(':')
        action = parts[0]
        previous_state = self.s.state(owner)
        if action == 'noop':
            return
        if action in ('home', 'cal', 'day', 'upcoming', 'note', 'cancel', 'help', 'jump', 'add', 'edit', 'alarm', 'delete', 'confirm', 'off'):
            self.s.set_state(owner)
        if action in ('home', 'cancel'):
            self.calendar(owner, now.year, now.month, now)
        elif action == 'cal' and len(parts) == 3:
            self.calendar(owner, int(parts[1]), int(parts[2]), now)
        elif action == 'day' and len(parts) == 3:
            self.listing(owner, parts[1], int(parts[2]), now)
        elif action == 'upcoming' and len(parts) == 2:
            self.listing(owner, None, int(parts[1]), now)
        elif action == 'jump':
            self.s.set_state(owner, {'mode': 'jump'})
            self.send(owner, message('Напишите дату: ДД.ММ.ГГГГ. Например 07.11.2026.', [[button('Отмена', 'cancel')]]))
        elif action == 'help':
            self.send(owner, message(HELP, [[button('Календарь', 'home')]]))
        elif action == 'add' and len(parts) == 2:
            date.fromisoformat(parts[1])
            self.s.set_state(owner, {'mode': 'add', 'day': parts[1]})
            self.send(owner, message(f'Новая заметка на {date_label(parts[1])}\n\nНапишите текст, до 3000 символов. '
                'Время можно указать прямо в тексте.\nНапример: 12:00 — встреча, обсудить детали.', [[button('Отмена', 'cancel')]]))
        elif action in ('note', 'edit', 'delete', 'confirm', 'alarm', 'off') and len(parts) == 2:
            n = self.note(owner, int(parts[1]))
            if not n:
                return
            nid = n['id']
            if action == 'note':
                self.send(owner, note_view(n))
            elif action == 'edit':
                self.s.set_state(owner, {'mode': 'edit', 'id': nid})
                self.send(owner, message('Пришлите новый текст целиком:\n\n' + n['text'], [[button('Отмена', f'note:{nid}')]]))
            elif action == 'delete':
                self.s.set_state(owner, {'mode': 'confirm', 'id': nid})
                self.send(owner, message('Удалить эту заметку?\n\n' + n['text'], [
                    [button('Да, удалить', f'confirm:{nid}'), button('Отмена', f'note:{nid}')]]))
            elif action == 'confirm':
                if previous_state != {'mode': 'confirm', 'id': nid}:
                    self.send(owner, message('Подтверждение устарело. Откройте запись и нажмите «Удалить» ещё раз.',
                                             [[button('Открыть запись', f'note:{nid}')]]))
                    return
                self.s.delete_note(owner, nid)
                self.send(owner, message('Заметка удалена. Напоминание отменено.'))
                self.listing(owner, n['day'], 0, now)
            elif action == 'off':
                self.s.alarm(owner, nid, None)
                self.send(owner, note_view(self.s.get_note(owner, nid)))
            elif action == 'alarm':
                self.s.set_state(owner, {'mode': 'alarm', 'id': nid})
                current = note_view(n)['text']
                self.send(owner, message(current + '\n\nКогда напомнить? Пришлите дату и время по Москве:\n'
                    'ДД.ММ.ГГГГ ЧЧ:ММ\nНапример: 07.11.2026 11:00.\n'
                    'Это отдельное время уведомления, а не время из текста заметки.', [
                        [button('Выключить напоминание', f'off:{nid}')], [button('Отмена', f'note:{nid}')]]))
        else:
            raise ValueError('Unknown callback')

    def text(self, owner, text, now):
        text = text.strip()
        command = text.split()[0].split('@')[0].lower() if text else ''
        if command.startswith('/'):
            self.s.set_state(owner)
            if command in ('/start', '/calendar', '/cancel'):
                if command == '/start':
                    self.send(owner, message('Ваш календарь записей. Выбирайте день и добавляйте заметки. '
                                            'Напоминания — по Москве. /help — инструкция.'))
                self.calendar(owner, now.year, now.month, now)
            elif command == '/today':
                self.listing(owner, now.date().isoformat(), 0, now)
            elif command == '/upcoming':
                self.listing(owner, None, 0, now)
            else:
                self.send(owner, message(HELP, [[button('Календарь', 'home')]]))
            return
        state = self.s.state(owner)
        if not state:
            self.send(owner, message('Сначала выберите день и нажмите «Добавить заметку».', [[button('Календарь', 'home')]]))
            return
        mode = state['mode']
        if mode == 'confirm':
            self.send(owner, message('Выберите «Да, удалить» или «Отмена» под запросом удаления, либо /cancel.'))
            return
        if mode in ('add', 'edit') and (not text or len(text.encode('utf-16-le')) // 2 > 3000):
            self.send(owner, message('Нужен текст от 1 до 3000 символов. Пришлите текст ещё раз или /cancel.'))
            return
        if mode == 'jump':
            try:
                day = parse_day(text)
            except ValueError as e:
                self.send(owner, message(str(e)))
                return
            self.s.set_state(owner)
            self.listing(owner, day.isoformat(), 0, now)
        elif mode == 'add':
            nid = self.s.add_note(owner, state['day'], text)
            self.s.set_state(owner)
            self.send(owner, message('Заметка сохранена. Напоминание пока выключено.'))
            self.send(owner, note_view(self.s.get_note(owner, nid)))
        elif mode in ('edit', 'alarm'):
            n = self.note(owner, state['id'])
            if not n:
                self.s.set_state(owner)
                return
            if mode == 'edit':
                self.s.edit_note(owner, n['id'], text)
                # Refresh any undelivered reminder rather than sending its stale note text.
                queued = self.s.db.execute('SELECT id FROM outbox WHERE note_id=?', (n['id'],)).fetchone()
                if queued:
                    view = reminder_view(self.s.get_note(owner, n['id']), now)
                    self.s.db.execute('UPDATE outbox SET payload=? WHERE id=?',
                        (json.dumps(dict(view, chat_id=owner), ensure_ascii=False), queued[0]))
            else:
                try:
                    at = parse_alarm(text, now)
                except ValueError as e:
                    self.send(owner, message(str(e)))
                    return
                self.s.alarm(owner, n['id'], at.isoformat(timespec='seconds'))
            self.s.set_state(owner)
            self.send(owner, note_view(self.s.get_note(owner, n['id'])))

    def queue_reminders(self, now):
        now = now.astimezone(MOSCOW)
        with self.s.db:
            for n in self.s.due(now):
                if n['owner'] in self.allowed:
                    self.s.enqueue('sendMessage', dict(reminder_view(n, now), chat_id=n['owner']), n['id'])
