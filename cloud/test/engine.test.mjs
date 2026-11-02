import test from 'node:test';
import assert from 'node:assert/strict';
import { fresh, interact, due } from '../src/engine.mjs';
const now=Date.parse('2026-11-02T12:00:00+03:00');
const msg=(id,text)=>({update_id:id,message:{message_id:id,from:{id:11},chat:{id:11,type:'private'},text}});
const cb=(id,data)=>({update_id:id,callback_query:{id:String(id),from:{id:11},data,message:{message_id:100,chat:{id:11,type:'private'}}}});
function flow(s,...updates){for(const u of updates)interact(s,u,now);return s;}
function add(s){flow(s,cb(1,'create'),msg(2,'Встреча'),cb(3,'pick:2026-11-07'),msg(4,'12:30'),cb(5,'save'));return s.notes[0];}
test('create with calendar and idempotent updates',()=>{const s=fresh();const n=add(s);assert.equal(n.time,'12:30');assert.equal(n.day,'2026-11-07');interact(s,cb(5,'save'),now);assert.equal(s.notes.length,1);});
test('manual date rejects invalid dates and accepts future',()=>{const s=fresh();flow(s,cb(1,'create'),msg(2,'План'),cb(3,'manual_date'),msg(4,'31.02.2026 14:30'));assert.equal(s.session.mode,'manual');flow(s,msg(5,'31.12.2026 14:30'),cb(6,'save'));assert.equal(s.notes[0].day,'2026-12-31');});
test('navigation edits existing panel and full year is outside narrow button',()=>{const s=fresh();flow(s,cb(1,'create'),msg(2,'План'),cb(3,'month:2026:12'));const e=s.effects.at(-1);assert.equal(e.method,'editMessageText');assert.match(e.payload.text,/Декабрь 2026/);assert.equal(e.payload.reply_markup.inline_keyboard[0].length,2);});
test('edit delete confirmation and canceled confirmation',()=>{const s=fresh();const n=add(s);flow(s,cb(6,`edit:${n.id}`),msg(7,'Новый текст'),cb(8,`delete:${n.id}`),cb(9,`open:${n.id}`),cb(10,`confirm:${n.id}`));assert.equal(s.notes[0].text,'Новый текст');flow(s,cb(11,`delete:${n.id}`),cb(12,`confirm:${n.id}`));assert.equal(s.notes.length,0);});
test('groups preserve notes on deletion',()=>{const s=fresh();const n=add(s);flow(s,cb(6,'group_new'),msg(7,'Работа'));const g=s.groups[0];flow(s,cb(8,`assign:${n.id}:${g.id}`),cb(9,`group_delete:${g.id}`),cb(10,`group_confirm:${g.id}`));assert.equal(s.notes.length,1);assert.equal(s.notes[0].group,null);});
test('media saved on draft and returned by file id',()=>{const s=fresh();flow(s,cb(1,'create'),msg(2,'Документы'));const u=msg(3,'');u.message.document={file_id:'pdf_file',file_name:'plan.pdf'};flow(s,u,cb(4,'pick:2026-11-07'),msg(5,'12:00'),cb(6,'save'));assert.equal(s.notes[0].files[0].file_id,'pdf_file');flow(s,cb(7,'file:1'));assert.equal(s.effects.at(-1).method,'sendDocument');});
test('reminders pause resume and no duplicate due event',()=>{const s=fresh();const n=add(s);flow(s,cb(6,`preset:${n.id}:60`));due(s,Date.parse('2026-11-07T11:31:00+03:00'));due(s,Date.parse('2026-11-07T11:32:00+03:00'));assert.equal(s.effects.filter(e=>e.reminder===n.id).length,1);flow(s,cb(7,'pause'));assert.equal(s.effects.filter(e=>e.reminder).length,0);flow(s,cb(8,'resume'));assert.equal(s.enabled,true);});
test('missing and stale buttons are safe',()=>{const s=fresh();flow(s,cb(1,'open:999'),cb(2,'month:99999:99'),cb(3,'file:999'));assert.equal(s.notes.length,0);});
test('restart serialization preserves draft and attachment',()=>{let s=fresh();flow(s,cb(1,'create'),msg(2,'План'));s=JSON.parse(JSON.stringify(s));flow(s,cb(3,'manual_date'),msg(4,'07.11.2026 12:00'),cb(5,'save'));assert.equal(s.notes[0].text,'План');});
