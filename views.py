import calendar
import re
from datetime import date, datetime, timedelta, timezone

MOSCOW = timezone(timedelta(hours=3), 'МСК')
MONTHS = ('', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
          'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь')
WEEKDAYS = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')


def button(text, data):
    return {'text': text, 'callback_data': data}


def message(text, rows=()):
    return {'text': text, 'reply_markup': {'inline_keyboard': list(rows)}}


def parse_day(text):
    if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', text.strip()):
        raise ValueError('Укажите дату в формате ДД.ММ.ГГГГ, например 07.11.2026.')
    try:
        d, m, y = map(int, text.strip().split('.'))
        return date(y, m, d)
    except ValueError:
        raise ValueError('Такой даты нет. Пример: 07.11.2026.') from None


def parse_alarm(text, now):
    text = text.strip()
    if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}', text):
        raise ValueError('Формат: ДД.ММ.ГГГГ ЧЧ:ММ. Например 07.11.2026 11:00 (МСК).')
    try:
        d = parse_day(text[:10])
        h, m = map(int, text[11:].split(':'))
        at = datetime(d.year, d.month, d.day, h, m, tzinfo=MOSCOW)
    except ValueError:
        raise ValueError('Проверьте дату и время. Пример: 07.11.2026 11:00.') from None
    if at <= now:
        raise ValueError('Это время уже прошло. Укажите будущее время по Москве.')
    return at


def date_label(day):
    d = date.fromisoformat(day)
    return f'{d.day:02}.{d.month:02}.{d.year:04} · {WEEKDAYS[d.weekday()]}'


def calendar_view(year, month, marked, today):
    date(year, month, 1)
    index = (year - 1) * 12 + month - 1
    def nav(delta, label):
        n = index + delta
        return button(label, f'cal:{n // 12 + 1}:{n % 12 + 1}') if 0 <= n < 9999 * 12 else button(' ', 'noop')
    rows = [[nav(-1, '‹'), button(f'{MONTHS[month]} {year}', 'jump'), nav(1, '›')],
            [button(w.title(), 'noop') for w in WEEKDAYS]]
    for week in calendar.Calendar().monthdayscalendar(year, month):
        row = []
        for num in week:
            if not num:
                row.append(button(' ', 'noop'))
                continue
            day = f'{year:04}-{month:02}-{num:02}'
            label = str(num) + (' •' if day in marked else '')
            if day == today.isoformat():
                label = f'[{label}]'
            row.append(button(label, f'day:{day}:0'))
        rows.append(row)
    rows.extend([[button('Сегодня', f'day:{today.isoformat()}:0'), button('К дате', 'jump')],
                 [button('Ближайшие записи', 'upcoming:0'), button('Помощь', 'help')]])
    return message('Календарь записей\nВыберите день. • — есть заметки, [ ] — сегодня.\nВремя напоминаний — Москва.', rows)


def note_view(note):
    alarm = 'выключено'
    if note['remind_at']:
        at = datetime.fromisoformat(note['remind_at'])
        alarm = at.strftime('%d.%m.%Y %H:%M') + ' МСК' + (' · отправлено' if note['reminded'] else '')
    n = note['id']
    event = ' · ' + note['event_time'] if note.get('event_time') else ''
    return message(f'{date_label(note["day"])}{event}\n\n{note["text"]}\n\nНапоминание: {alarm}', [
        [button('Изменить текст', f'edit:{n}'), button('Напоминание', f'alarm:{n}')],
        [button('Удалить', f'delete:{n}')],
        [button('← К записям дня', f'day:{note["day"]}:0')]])


def reminder_view(note, now):
    at = datetime.fromisoformat(note['remind_at'])
    late = '\nНапоминание пропущено во время простоя; доставляю сейчас.' if (now - at).total_seconds() > 120 else ''
    event = ' · ' + note['event_time'] if note.get('event_time') else ''
    return message(f'Напоминание\nЗапись на {date_label(note["day"])}{event}\n\n{note["text"]}'
                   f'\n\nНазначено на {at.strftime("%d.%m.%Y %H:%M")} МСК.{late}',
                   [[button('Открыть запись', f'note:{note["id"]}')]])
