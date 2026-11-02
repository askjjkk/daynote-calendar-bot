"""Export the local database into ignored SQL for a NEW, empty D1 database.

Contains private notes and file IDs: never commit the output. Run after stopping
the local process at cutover. SQLite backup is made by the main CLI separately.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from main import load_settings


def sql_string(value):
    return "'"+value.replace("'","''")+"'"


def export(root=ROOT):
    settings=load_settings(root)
    db=sqlite3.connect(f'file:{settings.db_path.as_posix()}?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        pending=db.execute('SELECT COUNT(*) FROM outbox WHERE error IS NULL').fetchone()[0]
        if pending:
            raise RuntimeError('Есть неотправленные ответы: дождитесь доставки перед финальным экспортом.')
        commands=[];counts={'owners':0,'notes':0,'groups':0,'attachments':0}
        offsetrow=db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
        offset=int(offsetrow[0]) if offsetrow else 0
        for owner in sorted(settings.allowed):
            groups=[dict(r) for r in db.execute('SELECT id,name FROM groups WHERE owner=? ORDER BY id',(owner,))]
            notes=[];max_file=0
            for r in db.execute('SELECT * FROM notes WHERE owner=? ORDER BY id',(owner,)):
                files=[]
                for f in db.execute('SELECT id,kind,file_id,label FROM attachments WHERE note_id=? ORDER BY id',(r['id'],)):
                    files.append(dict(f));max_file=max(max_file,f['id'])
                at=datetime.fromisoformat(r['remind_at']).timestamp()*1000 if r['remind_at'] else None
                notes.append({'id':r['id'],'day':r['day'],'time':r['event_time'],'text':r['text'],'group':r['group_id'],
                              'alarm':at,'reminded':bool(r['reminded']),'files':files})
                counts['attachments']+=len(files)
            enabled=db.execute('SELECT value FROM meta WHERE key=?',(f'enabled:{owner}',)).fetchone()
            state={'groups':groups,'session':None,'enabled':not enabled or enabled[0]=='1',
                   'nextNote':max([n['id'] for n in notes],default=0)+1,
                   'nextGroup':max([g['id'] for g in groups],default=0)+1,
                   'nextFile':max_file+1,'nextEffect':1,'effects':[],'seen':[],'minUpdate':offset}
            session=db.execute('SELECT value FROM states WHERE owner=?',(owner,)).fetchone()
            if session:
                old=json.loads(session[0]);mode={'draft_text':'text','draft_date':'date','draft_time':'time',
                    'draft_manual':'manual','draft_confirm':'confirm','edit':'edit','alarm':'alarm',
                    'attachments':'attachments','group_name':'group_name','group_rename':'group_rename',
                    'group_delete':'group_delete','confirm':'delete'}.get(old.get('mode'))
                if mode:
                    mapped=dict(old,mode=mode)
                    mapped['files']=[]
                    for f in old.get('files',[]):
                        mapped['files'].append(dict(f,id=state['nextFile']));state['nextFile']+=1
                    state['session']=mapped
            commands.append(f'INSERT INTO owners(owner,data) VALUES({owner},{sql_string(json.dumps(state,ensure_ascii=False))});')
            for n in notes:
                commands.append(f'INSERT INTO calendar_notes(owner,id,data) VALUES({owner},{n["id"]},{sql_string(json.dumps(n,ensure_ascii=False))});')
            counts['owners']+=1;counts['notes']+=len(notes);counts['groups']+=len(groups)
        target=root/'cloud/private';target.mkdir(parents=True,exist_ok=True)
        (target/'import.sql').write_text('\n'.join(commands)+'\n',encoding='utf8')
        (target/'expected-counts.json').write_text(json.dumps(counts),encoding='utf8')
        print('Подготовлен приватный SQL-импорт. Количество объектов:',json.dumps(counts))
        return counts
    finally:
        db.close()


if __name__=='__main__':
    export()
