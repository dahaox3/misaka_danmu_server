// Misaka adapter, 2026-10-20. GPL-3.0-or-later.
// Uses the unmodified xmcp/pakku.js combine_worker and similarity WASM.
import { readFile } from 'node:fs/promises';
import { parentPort } from 'node:worker_threads';
import { do_combine, prepare_combine } from './vendor/pakku/pakkujs/core/combine_worker';
import { processDensity } from './density';
import { select_latest, validate_latest_count } from './vendor/pakku/pakkujs/core/recent';

export function selectLatestComments(comments: any[], count: number) {
  validate_latest_count(count);
  try {
    return select_latest(comments.map(item => ({...item, sendtime: Number(item.sendtime), id: String(item.id ?? '')})), count);
  } catch (error) {
    if (error instanceof Error) error.name = 'SettingsError';
    throw error;
  }
}

const wasm = await readFile(new URL('./similarity-gen.wasm', import.meta.url));
await prepare_combine(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));

const likesRE = /\s+(?:[🤍🩵🩷❤♡🔥]\uFE0F?\s+\d+(?:\.\d+)?[wk]?|\[[👍🔥]\d+(?:\.\d+)?[wk]?\]|\((?:点赞|热门)\d+(?:\.\d+)?[wk]?\)|\+\d+(?:\.\d+)?[wk]?)$/u;
const repeatRE = /\s+X([2-9]\d*|1\d+)$/;
const prefixRE = /^\[([2-9]\d*|1\d+)\]\s+/;

export const DEFAULT_OPTIONS = {
  distanceEnabled: true, maxDistance: 5, cosineEnabled: true, cosineThreshold: 45,
  pinyin: true, trimEnding: true, trimSpace: true, trimWidth: true,
  crossMode: false, processScroll: true, processTop: true, processBottom: true,
  forceRules: [['^23{2,}$', '23333'], ['^6{3,}$', '66666']],
  forceContinue: true, forceApplySingles: false, whitelist: [],
  markPosition: 'prefix', markStyle: 'brackets', markMinCount: 2, mergeMinCount: 2,
  representativePercent: 0, textSelection: 'common', modeElevation: false,
  includeSourceCounts: true,
  enlarge: false, shrinkThreshold: 0, dropThreshold: 0,
};

export function validateOptions(input: any = {}) {
  try {
  if (!input || typeof input !== 'object' || Array.isArray(input) ||
      Object.keys(input).some(key => !Object.hasOwn(DEFAULT_OPTIONS, key))) throw Error('未知的合并参数');
  const o: any = { ...DEFAULT_OPTIONS, ...input };
  for (const [key, value] of Object.entries(DEFAULT_OPTIONS)) {
    if (typeof value === 'boolean' && typeof o[key] !== 'boolean') throw Error(`${key} 必须为开关值`);
  }
  for (const [key, min, max] of [['maxDistance', 0, 20], ['cosineThreshold', 1, 100],
    ['markMinCount', 2, 10000], ['mergeMinCount', 2, 10000], ['representativePercent', 0, 100],
    ['shrinkThreshold', 0, 1000], ['dropThreshold', 0, 1000]] as const) {
    if (!Number.isInteger(o[key]) || o[key] < min || o[key] > max) throw Error(`${key} 超出允许范围`);
  }
  for (const [key, choices] of [['markPosition', ['prefix', 'suffix', 'off']],
    ['markStyle', ['brackets', 'pakku', 'subscript']], ['textSelection', ['common', 'first', 'shortest', 'longest']]] as const) {
    if (!(choices as readonly string[]).includes(o[key])) throw Error(`${key} 无效`);
  }
  if (!Array.isArray(o.forceRules) || o.forceRules.length > 50 || !Array.isArray(o.whitelist) || o.whitelist.length > 50)
    throw Error('每组例外规则最多50条');
  o.forceRules.forEach((rule: any, i: number) => {
    if (!Array.isArray(rule) || rule.length !== 2 || rule.some(x => typeof x !== 'string' || x.length > 200) || !rule[0])
      throw Error(`替换规则第${i + 1}行格式不正确`);
    try { new RegExp(rule[0], 'uig'); } catch { throw Error(`替换规则第${i + 1}行不是有效正则表达式`); }
  });
  o.whitelist.forEach((rule: any, i: number) => {
    if (typeof rule !== 'string' || !rule || rule.length > 200) throw Error(`白名单第${i + 1}行格式不正确`);
    try { new RegExp(rule, 'ui'); } catch { throw Error(`白名单第${i + 1}行不是有效正则表达式`); }
  });
  return o;
  } catch (error) {
    if (error instanceof Error) error.name = 'SettingsError';
    throw error;
  }
}

function decorate(text: string, count: number, o: any) {
  if (count < o.markMinCount || o.markPosition === 'off') return text;
  const mark = o.markStyle === 'subscript' ? `₍${String(count).replace(/\d/g, c => '₀₁₂₃₄₅₆₇₈₉'[Number(c)])}₎` :
    o.markStyle === 'pakku' ? `[x${count}]` : `[${count}]`;
  return o.markPosition === 'prefix' ? `${mark} ${text}` : `${text} ${mark}`;
}

export async function merge(payload: any) {
  let { comments, windowSeconds = 30, mode = 'exact' } = payload;
  if (!Array.isArray(comments) || comments.length > 200000 ||
      !Number.isFinite(windowSeconds) || windowSeconds < 1 || windowSeconds > 120 ||
      !['exact', 'similar'].includes(mode)) throw Error('invalid input');
  if (payload.selectionOnly) {
    const selected = selectLatestComments(comments, payload.latestCount ?? 0);
    return {comments: selected, inputCount: comments.length, outputCount: selected.length, grouped: 0};
  }
  const inputCount = comments.length;
  if (payload.latestCount) comments = selectLatestComments(comments, payload.latestCount);
  const smart = mode === 'similar';
  const options = validateOptions(payload.options);
  const config: any = {
    THRESHOLD: windowSeconds, MAX_DIST: smart && options.distanceEnabled ? options.maxDistance : -1,
    MAX_COSINE: smart && options.cosineEnabled ? options.cosineThreshold : 101,
    TRIM_PINYIN: smart && options.distanceEnabled && options.pinyin,
    TRIM_ENDING: smart && options.trimEnding, TRIM_SPACE: smart && options.trimSpace, TRIM_WIDTH: smart && options.trimWidth,
    FORCELIST: smart ? options.forceRules : [],
    FORCELIST_CONTINUE_ON_MATCH: options.forceContinue, FORCELIST_APPLY_SINGULAR: options.forceApplySingles,
    WHITELIST: options.whitelist.map((x: string) => [x, '']), BLACKLIST: [], CROSS_MODE: options.crossMode,
    PROC_POOL1: false, PROC_TYPE7: false, PROC_TYPE4: true,
  };
  const unchanged: any[] = [];
  const entries: any[] = [];
  comments.forEach((item: any, originalIndex: number) => {
    if (typeof item?.p !== 'string' || typeof item?.m !== 'string') throw Error('invalid comment');
    const parts = item.p.split(',');
    const time = Number(parts[0]);
    const type = Number(parts[1]);
    const participate = (type === 1 || type === 6) ? options.processScroll : type === 4 ? options.processBottom :
      type === 5 ? options.processTop : false;
    if (!Number.isFinite(time) || !participate || item.m.length > 4000 || !item.m.trim()) {
      unchanged.push({ item, time: Number.isFinite(time) ? time : Infinity, originalIndex });
      return;
    }
    let body = item.m;
    let likes = '';
    let weight = 1;
    // Both historical "body X10 🤍 5" and "body 🤍 5 X10" are accepted.
    for (let i = 0; i < 3; i++) {
      const like = body.match(likesRE);
      if (like) { likes = like[0]; body = body.slice(0, -like[0].length); continue; }
      const repeat = body.match(repeatRE);
      if (repeat && weight === 1) {
        weight = Number(repeat[1]); body = body.slice(0, -repeat[0].length); continue;
      }
      break;
    }
    const prefix = body.match(prefixRE);
    if (prefix && weight === 1) { weight = Number(prefix[1]); body = body.slice(prefix[0].length); }
    if (!Number.isSafeInteger(weight) || weight > 10000000 || !body.trim()) {
      unchanged.push({ item, time, originalIndex }); return;
    }
    if (!options.includeSourceCounts) weight = 1;
    const n = likes.match(/(\d+(?:\.\d+)?)([wk]?)/);
    const likesCount = Math.max(Number(item._pakku_likes) || 0, n ? Number(n[1]) * (n[2] === 'w' ? 10000 : n[2] === 'k' ? 1000 : 1) : 0);
    const priority = Math.max(1, Math.min(11, Math.floor(Number(item.weight || item.w) || 1)));
    entries.push({ item, time, type, originalIndex, body, likes, weight, likesCount, priority });
  });
  entries.sort((a, b) => a.time - b.time || a.originalIndex - b.originalIndex);
  const objs = entries.map((x, i) => ({
    time_ms: Math.round(x.time * 1000), mode: x.type, fontsize: 25, color: 16777215,
    sender_hash: '', content: x.body, sendtime: 0, weight: 0, id: String(i), pool: 0, extra: {},
  }));
  const result = await do_combine({ objs, extra: {} }, { objs: [], extra: {} }, config);
  let grouped = 0;
  const output = result.clusters.flatMap(cluster => {
    const peers = cluster.peers_ptr.map(([idx]) => entries[idx]);
    const count = peers.reduce((n, x) => n + x.weight, 0);
    if (peers.length > 1 && count < options.mergeMinCount) {
      return peers.map(x => ({ item: { ...x.item, m: decorate(x.body, x.weight, options) + x.likes,
        _pakku_repeat_count: x.weight, _pakku_likes: x.likesCount, _pakku_weight: x.priority }, time: x.time, originalIndex: x.originalIndex }));
    }
    const representative = peers[Math.min(Math.floor(peers.length * options.representativePercent / 100), peers.length - 1)];
    // Keep a real representative's likes; these are not the repetition count.
    const decorated = peers.find(x => x.likes) || representative;
    if (count > 1) grouped++;
    let text = cluster.chosen_str;
    if (peers.length > 1) {
      if (options.textSelection === 'first') text = peers[0].body;
      if (options.textSelection === 'shortest') text = peers.reduce((a, b) => a.body.length <= b.body.length ? a : b).body;
      if (options.textSelection === 'longest') text = peers.reduce((a, b) => a.body.length >= b.body.length ? a : b).body;
    }
    let p = representative.item.p;
    if (options.crossMode && options.modeElevation) {
      const mode = peers.some(x => x.type === 4) ? 4 : peers.some(x => x.type === 5) ? 5 : representative.type;
      const parts = p.split(','); parts[1] = String(mode); p = parts.join(',');
    }
    return [{
      item: { ...representative.item, p, m: decorate(text, count, options) + decorated.likes, _pakku_repeat_count: count,
        _pakku_likes: peers.reduce((n, x) => n + x.likesCount, 0), _pakku_weight: peers.reduce((n, x) => Math.max(n, x.priority), 1) },
      time: representative.time, originalIndex: representative.originalIndex,
    }];
  });
  output.push(...unchanged);
  output.sort((a, b) => a.time - b.time || a.originalIndex - b.originalIndex);
  const selected = processDensity(output, options, result.stats);
  return { comments: selected.map(x => x.item), inputCount, selectedCount: comments.length, outputCount: selected.length,
    grouped: selected.filter(x => x.item._pakku_repeat_count > 1).length, stats: result.stats, options };
}

parentPort?.on('message', async ({ id, payload }) => {
  try { parentPort!.postMessage({ id, result: await merge(payload) }); }
  catch (error) { parentPort!.postMessage({ id, error: error instanceof Error && error.name === 'SettingsError' ? error.message : 'merge failed',
    invalidSettings: error instanceof Error && error.name === 'SettingsError' }); }
});
parentPort?.postMessage({ ready: true });
