"""SQLite storage. Bot.handle owns the transaction for each Telegram update."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL, day TEXT NOT NULL,
                text TEXT NOT NULL, remind_at TEXT, reminded INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS notes_owner_day ON notes(owner,day,id);
            CREATE TABLE IF NOT EXISTS states (owner INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, method TEXT NOT NULL, payload TEXT NOT NULL,
                note_id INTEGER UNIQUE REFERENCES notes(id) ON DELETE CASCADE,
                attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                error TEXT);
        ''')

    def close(self):
        self.db.close()

    def offset(self):
        r = self.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
        return int(r[0]) if r else 0

    def set_offset(self, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('offset',?)", (str(value),))

    def state(self, owner):
        r = self.db.execute('SELECT value FROM states WHERE owner=?', (owner,)).fetchone()
        return json.loads(r[0]) if r else None

    def set_state(self, owner, value=None):
        self.db.execute('DELETE FROM states WHERE owner=?', (owner,))
        if value is not None:
            self.db.execute('INSERT INTO states VALUES (?,?)', (owner, json.dumps(value)))

    def get_note(self, owner, note_id):
        r = self.db.execute('SELECT * FROM notes WHERE owner=? AND id=?', (owner, note_id)).fetchone()
        return dict(r) if r else None

    def notes(self, owner, day):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM notes WHERE owner=? AND day=? ORDER BY id', (owner, day))]

    def upcoming(self, owner, today):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM notes WHERE owner=? AND day>=? ORDER BY day,id', (owner, today))]

    def marked_days(self, owner, year, month):
        return {r[0] for r in self.db.execute(
            'SELECT DISTINCT day FROM notes WHERE owner=? AND day LIKE ?',
            (owner, f'{year:04}-{month:02}-%'))}

    def add_note(self, owner, day, text):
        return self.db.execute('INSERT INTO notes(owner,day,text) VALUES(?,?,?)',
                               (owner, day, text)).lastrowid

    def edit_note(self, owner, note_id, text):
        self.db.execute('UPDATE notes SET text=? WHERE owner=? AND id=?', (text, owner, note_id))

    def delete_note(self, owner, note_id):
        self.db.execute('DELETE FROM notes WHERE owner=? AND id=?', (owner, note_id))

    def alarm(self, owner, note_id, at):
        if not self.get_note(owner, note_id):
            return
        self.db.execute('DELETE FROM outbox WHERE note_id=?', (note_id,))
        self.db.execute('UPDATE notes SET remind_at=?,reminded=0 WHERE owner=? AND id=?',
                        (at, owner, note_id))

    def due(self, now):
        return [dict(r) for r in self.db.execute(
            'SELECT * FROM notes WHERE remind_at IS NOT NULL AND reminded=0 AND remind_at<=?',
            (now.isoformat(timespec='seconds'),))]

    def enqueue(self, method, payload, note_id=None):
        self.db.execute('INSERT OR IGNORE INTO outbox(method,payload,note_id) VALUES(?,?,?)',
                        (method, json.dumps(payload, ensure_ascii=False), note_id))

    def pending(self, at=float('inf')):
        result = []
        for r in self.db.execute('SELECT * FROM outbox WHERE error IS NULL AND retry_at<=? ORDER BY id', (at,)):
            v = dict(r)
            v['payload'] = json.loads(v['payload'])
            result.append(v)
        return result

    def delivered(self, row_id):
        r = self.db.execute('SELECT note_id FROM outbox WHERE id=?', (row_id,)).fetchone()
        if r and r[0] is not None:
            self.db.execute('UPDATE notes SET reminded=1 WHERE id=?', (r[0],))
        self.db.execute('DELETE FROM outbox WHERE id=?', (row_id,))

    def failed(self, row_id, retry_at=None, error=None):
        self.db.execute('UPDATE outbox SET attempts=attempts+1,retry_at=?,error=? WHERE id=?',
                        (retry_at or 0, error, row_id))

    def backup(self, path):
        dest = sqlite3.connect(str(path))
        try:
            self.db.backup(dest)
        finally:
            dest.close()
