// Adapted from xmcp/pakku.js core/post_combine.ts, GPL-3.0-or-later.
// Same 5-second density / enlargement / shrink / quality formulas.
// A stable hash replaces Math.random for repeatable server output and previews.
const small = new Set('₍₀₁₂₃₄₅₆₇₈₉₎↓↑');
for (let x = 0x20; x <= 0x7e; x++) small.add(String.fromCharCode(x));

function chance(text: string) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  return (hash >>> 0) / 4294967296;
}

export function processDensity(rows: any[], options: any, stats: any) {
  stats.modified_enlarge = 0; stats.modified_shrink = 0; stats.deleted_dispval = 0; stats.num_max_dispval = 0;
  if (!options.enlarge && !options.shrinkThreshold && !options.dropThreshold) return rows;
  const managed = rows.filter(x => x.item._pakku_repeat_count);
  const distribution = Array(12).fill(0);
  for (const row of managed) distribution[Math.max(1, Math.min(11, row.item._pakku_weight || 1))]++;
  for (let i = 1; i <= 11; i++) {
    distribution[i] /= managed.length || 1;
    distribution[i] += distribution[i - 1];
    distribution[i - 1] = Math.pow((distribution[i - 1] + distribution[i]) / 2, 3);
  }
  const active: [number, number][] = [];
  let left = 0, density = 0;
  return rows.filter(row => {
    const item = row.item;
    if (!item._pakku_repeat_count || !Number.isFinite(row.time)) return true;
    const parts = item.p.split(',');
    const sourceAt = parts.findIndex((x: string) => x.includes('[') && x.includes(']'));
    const core = sourceAt < 0 ? parts.length : sourceAt;
    let size = Number(item.fontSize || (core === 4 ? parts[2] : 25));
    if (!Number.isFinite(size) || size <= 0) size = 25;
    const count = item._pakku_repeat_count;
    if (options.enlarge && count > 5) {
      size = Math.ceil(size * Math.log(count) / Math.log(5)); stats.modified_enlarge++;
    }
    size = Math.max(8, Math.min(96, size));
    const text = String(item.m).replace(/[\r\n\t]/g, '').trim();
    const short = [...text].filter(c => small.has(c)).length;
    const value = Math.sqrt(Math.max(0, text.length - short / 2)) * Math.pow(Math.max(.7, Math.min(2.5, size / 25)), 1.5);
    while (left < active.length && row.time > active[left][0]) density -= active[left++][1];
    if (options.dropThreshold > 0 && density > options.dropThreshold) {
      const weight = Math.max(1, Math.min(11, item._pakku_weight || 1));
      const rate = (density - options.dropThreshold) / options.dropThreshold + .25 - distribution[weight - 1] / 4 -
        (Math.sqrt(count) - 1) / 5 - Math.sqrt(item._pakku_likes || 0) / 8;
      if (rate >= 1 || (rate > 0 && chance(item.p + '\0' + item.m) < rate)) {
        stats.deleted_dispval++; return false;
      }
    }
    density += value; active.push([row.time + 5, value]);
    stats.num_max_dispval = Math.max(stats.num_max_dispval, density);
    if (options.shrinkThreshold > 0 && density > options.shrinkThreshold) {
      size /= Math.min(Math.pow(density / options.shrinkThreshold, .35), 1.732); stats.modified_shrink++;
    }
    if (options.enlarge || options.shrinkThreshold > 0) {
      item.fontSize = Math.round(Math.max(8, Math.min(96, size)) * 100) / 100;
      if (core === 4) { parts[2] = String(item.fontSize); item.p = parts.join(','); }
    }
    return true;
  });
}
