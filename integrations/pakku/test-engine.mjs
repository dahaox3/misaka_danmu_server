import { test } from 'node:test';
import assert from 'node:assert/strict';
import { merge } from './engine.mjs';
const dm = (time, text, mode = 1, color = 16777215) => ({ p: `${time},${mode},25,${color},[bilibili]`, m: text });

test('duplicates collapse inside a fixed 30 second window; input is unchanged', async () => {
  const input = [dm(0, '前方高能'), dm(1, '前方高能'), dm(30, '前方高能'), dm(30.01, '前方高能')];
  const original = structuredClone(input);
  const { comments } = await merge({ comments: input });
  assert.deepEqual(comments.map(x => x.m), ['[3] 前方高能', '前方高能']);
  assert.deepEqual(input, original);
  assert.equal(comments[0].p, input[0].p);
});
test('identical mode does not merge anagrams, punctuation, or similar sentences', async () => {
  const input = [dm(0, '我喜欢你'), dm(1, '你喜欢我'), dm(2, '我喜欢你！')];
  assert.equal((await merge({ comments: input })).comments.length, 3);
});
test('directions remain separate and special/invalid comments pass through', async () => {
  const input = [dm(0, '哈哈', 1), dm(1, '哈哈', 5), dm(2, '哈哈', 4), dm(3, '[1,2,3]', 7), { p: 'bad', m: '原样' }];
  const result = await merge({ comments: input });
  assert.deepEqual(result.comments.map(x => Object.fromEntries(Object.entries(x).filter(([key]) => !key.startsWith('_pakku_')))), input);
});
test('historical counts sum correctly, likes stay distinct, leading prefix is idempotent', async () => {
  const input = [dm(0, '前方高能 X10 ❤️ 5'), dm(1, '前方高能 🤍 20 X3'), dm(2, '前方高能')];
  const { comments } = await merge({ comments: input });
  assert.equal(comments[0].m, '[14] 前方高能 ❤️ 5');
  assert.deepEqual((await merge({ comments })).comments, comments);
});
test('a singleton upstream count becomes a prefix and normal singleton remains unchanged', async () => {
  assert.deepEqual((await merge({ comments: [dm(0, '测试 X12'), dm(31, '另一个')] })).comments.map(x => x.m), ['[12] 测试', '另一个']);
});
test('uses original pakku normalization and smart algorithm', async () => {
  const result = await merge({ comments: [dm(0, '前方高能！'), dm(1, '前方高能'), dm(2, '６６６６６')], mode: 'similar' });
  assert.equal(result.comments.length, 2);
  assert.equal(result.comments[0].m, '[2] 前方高能');
  assert.equal(result.stats.combined_identical, 1);
});
test('sorting is stable, colors, metadata and representative timestamp survive', async () => {
  const first = { ...dm(1, '你好', 1, 12345), extra: 'keep' };
  const input = [dm(15, '你好'), first, dm(1, '再见')];
  const result = await merge({ comments: input });
  assert.equal(result.comments[0].m, '[2] 你好');
  assert.equal(result.comments[0].p, first.p);
  assert.equal(result.comments[0].extra, 'keep');
});
test('invalid settings are rejected and empty input succeeds', async () => {
  assert.deepEqual((await merge({ comments: [] })).comments, []);
  await assert.rejects(() => merge({ comments: [], windowSeconds: 999 }));
  await assert.rejects(() => merge({ comments: [null] }));
});

test('algorithm thresholds and normalization switches change real clustering', async () => {
  const comments = [dm(0, '前方高能！'), dm(1, '前方高能')];
  const strict = { distanceEnabled: false, cosineEnabled: false, trimEnding: false, trimWidth: false, trimSpace: false, forceRules: [] };
  assert.equal((await merge({ comments, mode: 'similar', options: strict })).outputCount, 2);
  assert.equal((await merge({ comments, mode: 'similar', options: { ...strict, trimEnding: true } })).outputCount, 1);
  const different = [dm(0, '这就是我们今天的胜利'), dm(1, '这就是我们今天的胜利啊')];
  assert.equal((await merge({ comments: different, mode: 'similar', options: { ...strict, distanceEnabled: true, pinyin: false, maxDistance: 0 } })).outputCount, 2);
  assert.equal((await merge({ comments: different, mode: 'similar', options: { ...strict, distanceEnabled: true, pinyin: false, maxDistance: 1 } })).outputCount, 1);
});
test('cosine matching can be disabled and its threshold changes grouping', async () => {
  const comments = [dm(0, 'abcdef'), dm(1, 'abcdeg')];
  const options = { distanceEnabled: false, trimEnding: false, cosineEnabled: true, cosineThreshold: 1 };
  assert.equal((await merge({ comments, mode: 'similar', options })).outputCount, 1);
  assert.equal((await merge({ comments, mode: 'similar', options: { ...options, cosineThreshold: 100 } })).outputCount, 2);
  assert.equal((await merge({ comments, mode: 'similar', options: { ...options, cosineEnabled: false } })).outputCount, 2);
});
test('whitelist, mode participation, crossing and mode elevation work', async () => {
  assert.equal((await merge({ comments: [dm(0, '保护'), dm(1, '保护')], options: { whitelist: ['^保护$'] } })).outputCount, 2);
  const comments = [dm(0, '相同', 1), dm(1, '相同', 4), dm(2, '相同', 5)];
  const result = await merge({ comments, options: { crossMode: true, modeElevation: true } });
  assert.equal(result.outputCount, 1); assert.equal(result.comments[0].p.split(',')[1], '4');
  assert.equal((await merge({ comments: [dm(0, '顶部', 5), dm(1, '顶部', 5)], options: { processTop: false } })).outputCount, 2);
});
test('rule chaining, replacement captures and singleton application use pakku', async () => {
  const options = { forceRules: [['^(旧)(文)$', '$1内容'], ['旧内容', '新内容']], forceApplySingles: true, forceContinue: true };
  assert.equal((await merge({ comments: [dm(0, '旧文')], mode: 'similar', options })).comments[0].m, '新内容');
  assert.equal((await merge({ comments: [dm(0, '旧文')], mode: 'similar', options: { ...options, forceContinue: false } })).comments[0].m, '旧内容');
  assert.equal((await merge({ comments: [dm(0, '旧文')], mode: 'similar', options: { ...options, forceApplySingles: false } })).comments[0].m, '旧文');
});
test('display thresholds, suffix, hidden marks and subscript count formatting work', async () => {
  const comments = [dm(0, '测试'), dm(1, '测试')];
  for (const [options, expected] of [
    [{ markPosition: 'suffix' }, '测试 [2]'], [{ markStyle: 'pakku' }, '[x2] 测试'],
    [{ markStyle: 'subscript' }, '₍₂₎ 测试'], [{ markPosition: 'off' }, '测试'], [{ markMinCount: 3 }, '测试'],
  ]) {
    const result = await merge({ comments, options });
    assert.equal(result.comments[0].m, expected);
    assert.equal(result.comments[0]._pakku_repeat_count, 2);
  }
  assert.equal((await merge({ comments, options: { mergeMinCount: 3 } })).outputCount, 2);
});
test('representative timestamp, original text choices and source weights are configurable', async () => {
  const comments = [dm(0, '前方高能！'), dm(1, '前方高能'), dm(2, '前方高能')];
  const result = await merge({ comments, mode: 'similar', options: { representativePercent: 100, textSelection: 'first' } });
  assert.equal(result.comments[0].p.split(',')[0], '2'); assert.equal(result.comments[0].m, '[3] 前方高能！');
  assert.equal((await merge({ comments, mode: 'similar', options: { textSelection: 'shortest' } })).comments[0].m, '[3] 前方高能');
  assert.equal((await merge({ comments: [dm(0, '你好 X10'), dm(1, '你好')], options: { includeSourceCounts: false } })).comments[0].m, '[2] 你好');
});
test('invalid regex, oversized rules and unknown settings are rejected before processing', async () => {
  await assert.rejects(() => merge({ comments: [], options: { forceRules: [['[', 'x']] } }), /替换规则第1行/);
  await assert.rejects(() => merge({ comments: [], options: { whitelist: ['('] } }), /白名单第1行/);
  await assert.rejects(() => merge({ comments: [], options: { maxDistance: 999 } }), /范围/);
  await assert.rejects(() => merge({ comments: [], options: { unknown: 1 } }), /未知/);
});

test('enlargement uses weighted repeats, preserves color and is disabled by default', async () => {
  const comments = [dm(0, '重要内容 X100', 1, 123456)];
  const baseline = await merge({ comments });
  assert.equal(baseline.comments[0].fontSize, undefined);
  const result = await merge({ comments, options: { enlarge: true } });
  assert.equal(result.comments[0].fontSize, Math.ceil(25 * Math.log(100) / Math.log(5)));
  assert.equal(result.comments[0].p.split(',')[3], '123456');
  assert.equal(result.stats.modified_enlarge, 1);
  assert.equal(comments[0].p.split(',')[2], '25');
});
test('density shrinks fonts, keeps colors, and expires after a gap', async () => {
  const result = await merge({ comments: [dm(0, '密集测试'), dm(0.1, '别的文字'), dm(6, '间隔之后')],
    options: { shrinkThreshold: 2 } });
  assert.ok(result.comments[1].fontSize < 25);
  assert.equal(result.comments[2].fontSize, 25);
  assert.equal(result.comments[1].p.split(',')[3], '16777215');
  assert.ok(result.stats.modified_shrink > 0);
});
test('quality filtering reduces dense output reproducibly and preserves valuable groups', async () => {
  const comments = Array.from({ length: 100 }, (_, i) => dm(0, `互不相同的示例内容-${i}`));
  comments.push(dm(0.1, '重要内容 X10000 🔥 1w'));
  const options = { dropThreshold: 20 };
  const result = await merge({ comments, options });
  const repeated = await merge({ comments, options });
  assert.ok(result.outputCount < comments.length);
  assert.equal(result.stats.deleted_dispval, comments.length - result.outputCount);
  assert.ok(result.comments.some(x => x.m.includes('重要内容')));
  assert.deepEqual(result, repeated);
});
test('types excluded from processing are not enlarged or density-filtered', async () => {
  const item = dm(0, '字幕样式 X100', 5);
  const result = await merge({ comments: [item], options: { processTop: false, enlarge: true, shrinkThreshold: 1, dropThreshold: 1 } });
  assert.deepEqual(result.comments, [item]);
});
