import {test} from 'node:test';
import assert from 'node:assert/strict';
import {merge, selectLatestComments} from './engine.mjs';

const row = (id, sendtime, time, text = id) => ({id, sendtime, p: `${time},1,16777215,[bilibili]`, m: text});

test('publication date wins over playback position; large IDs remain exact', () => {
  const rows = [row('1', 10, 999), row('90071992547409930', 20, 0), row('90071992547409931', 20, 1)];
  assert.equal(selectLatestComments(rows, 1)[0].id, '90071992547409931');
  assert.deepEqual(rows.map(r => r.id), ['1', '90071992547409930', '90071992547409931']);
});
test('unknown dates never fill a recent selection; missing dates are explicit', () => {
  assert.equal(selectLatestComments([row('1', 0, 0), row('2', 100, 9)], 5000).length, 1);
  assert.throws(() => selectLatestComments([row('1', 0, 0)], 3000), /发送日期/);
});
test('old duplicates are removed before counting or merging', async () => {
  const rows = [row('1', 100, 0, 'same'), row('2', 200, 1, 'same'), row('3', 300, 2, 'same')];
  const out = await merge({comments: rows, latestCount: 2, options: {trimEnding: false}});
  assert.equal(out.inputCount, 3);
  assert.equal(out.selectedCount, 2);
  assert.equal(out.outputCount, 1);
  assert.equal(out.comments[0]._pakku_repeat_count, 2);
  assert.equal(out.comments[0].m, '[2] same');
});
test('selection-only shares the user fork algorithm without merging', async () => {
  const rows = [row('1', 100, 2, 'same'), row('2', 200, 0, 'same')];
  const out = await merge({comments: rows, latestCount: 1, selectionOnly: true});
  assert.equal(out.comments[0].id, '2');
  assert.equal(out.outputCount, 1);
  assert.equal(out.comments[0].m, 'same');
});
test('zero preserves all and invalid counts are rejected', () => {
  const rows = [row('1', 0, 1)];
  assert.equal(selectLatestComments(rows, 0).length, 1);
  for (const count of [-1, 1.5, NaN, 1000001]) assert.throws(() => selectLatestComments(rows, count));
});
