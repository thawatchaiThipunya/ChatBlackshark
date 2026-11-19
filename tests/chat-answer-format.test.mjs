import test from 'node:test';
import assert from 'node:assert/strict';
import { parseChatAnswer } from '../src/lib/chat-answer-format.ts';

test('keeps comparison intro, table and recommendation in order', () => {
  const blocks = parseChatAnswer('สองรุ่นต่างกันดังนี้ครับ\n\n| เรื่อง | GS3 | GS3 Ultra |\n|---|---|---|\n| แบต | 21 วัน | 18 วัน |\n\nถ้าเน้นแบต เลือก GS3 ครับ');
  assert.deepEqual(blocks, [
    { type: 'text', text: 'สองรุ่นต่างกันดังนี้ครับ' },
    { type: 'table', headers: ['เรื่อง', 'GS3', 'GS3 Ultra'], rows: [['แบต', '21 วัน', '18 วัน']] },
    { type: 'text', text: 'ถ้าเน้นแบต เลือก GS3 ครับ' },
  ]);
});

test('keeps ordinary chat as text', () => {
  assert.deepEqual(parseChatAnswer('GS3 Ultra ใช้งานได้ 18 วันครับ'), [
    { type: 'text', text: 'GS3 Ultra ใช้งานได้ 18 วันครับ' },
  ]);
});

test('does not turn a malformed table into a table', () => {
  assert.deepEqual(parseChatAnswer('| เรื่อง | GS3 |\n| แบต | 21 วัน |'), [
    { type: 'text', text: '| เรื่อง | GS3 |\n| แบต | 21 วัน |' },
  ]);
});
