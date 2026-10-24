// Local pakku output settings and preview, 2026-10-20. AGPLv3.
import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Switch, Table, Tabs } from 'antd'
import api from '../../../apis/fetch'
import { useMessage } from '../../../MessageContext'

const DEFAULTS = {
  distanceEnabled: true, maxDistance: 5, cosineEnabled: true, cosineThreshold: 45,
  pinyin: true, trimEnding: true, trimSpace: true, trimWidth: true,
  crossMode: false, processScroll: true, processTop: true, processBottom: true,
  forceRules: [['^23{2,}$', '23333'], ['^6{3,}$', '66666']],
  forceContinue: true, forceApplySingles: false, whitelist: [],
  markPosition: 'prefix', markStyle: 'brackets', markMinCount: 2, mergeMinCount: 2,
  representativePercent: 0, textSelection: 'common', modeElevation: false, includeSourceCounts: true,
  enlarge: false, shrinkThreshold: 0, dropThreshold: 0,
}
const RecentCountInput = ({value = 0, onChange, id}) => <Space.Compact>
  <Select aria-label="最新发送条数预设" style={{width: 180}}
    value={[0, 1000, 3000, 5000, 10000].includes(value) ? value : undefined} placeholder="自定义条数"
    onChange={onChange} options={[{value: 0, label: '全部（原输出方式）'}, ...[1000, 3000, 5000, 10000].map(n => ({value: n, label: `最新 ${n} 条`}))]} />
  <InputNumber id={id} aria-label="最新发送条数" min={0} max={1000000} precision={0} value={value} onChange={x => onChange(x ?? 0)} />
</Space.Compact>
const displayDate = stamp => stamp ? new Date(stamp * 1000).toLocaleString() : '无'
const recentExample = () => {
  const now = Math.floor(Date.now() / 1000)
  return Array.from({length: 9}, (_, i) => `${i === 0 ? 900 : i} | 滚动 | ${i < 6 ? '同组新旧弹幕' : `近期弹幕${i}`} | ${now - (8 - i) * 86400} | ${90071992547409930n + BigInt(i)}`).join('\n')
}
const editable = o => ({ ...o, forceRulesText: JSON.stringify(o.forceRules, null, 2), whitelistText: o.whitelist.join('\n') })
const EXAMPLE = '0 | 滚动 | 前方高能\n1 | 滚动 | 前方高能！\n2 | 滚动 | 前方高能 X10\n3 | 顶部 | 前方高能\n4 | 滚动 | 保护这条弹幕\n5 | 滚动 | 保护这条弹幕\n7 | 滚动 | 666666\n8 | 滚动 | 666\n40 | 滚动 | 前方高能'
const positions = { '滚动': 1, '顶部': 5, '底部': 4, '逆向': 6 }
const ThresholdInput = ({ value = 0, onChange, id, label }) => <Space.Compact>
  <Select style={{ width: 150 }} value={[0, 50, 75, 120].includes(value) ? value : undefined}
    placeholder="自定义" onChange={onChange} aria-label={`${label}预设`} options={[
      { value: 0, label: '禁用' }, { value: 120, label: '轻微（>120）' },
      { value: 75, label: '中等（>75）' }, { value: 50, label: '强力（>50）' },
    ]} />
  <InputNumber id={id} min={0} max={1000} value={value} onChange={x => onChange(x ?? 0)} aria-label={label} />
</Space.Compact>
function denseExample() {
  let seed = 12345
  return ['0 | 滚动 | 重复弹幕示例 X100', ...Array.from({ length: 100 }, (_, i) => {
    const text = Array.from({ length: 14 }, () => { seed = Math.imul(seed, 1664525) + 1013904223 | 0; return String.fromCharCode(0x4e00 + ((seed >>> 0) % 20000)) }).join('')
    return `${(i / 25).toFixed(2)} | 滚动 | ${text}`
  })].join('\n')
}
const errorText = error => typeof error?.message === 'string' ? error.message :
  typeof error?.detail === 'string' ? error.detail : '参数格式或范围不正确，请检查后重试'

function parseRules(text) {
  const value = JSON.parse(text || '[]')
  if (!Array.isArray(value) || value.length > 50 || value.some(x =>
    !Array.isArray(x) || x.length !== 2 || x.some(y => typeof y !== 'string' || y.length > 200) || !x[0])) {
    throw Error('使用二维数组：每条是 ["正则表达式", "替换文本"]，最多50条、每项最多200字符')
  }
  return value
}

export const PakkuSettings = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [example, setExample] = useState(EXAMPLE)
  const [previewing, setPreviewing] = useState(false)
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState('')
  const [recentEpisodes, setRecentEpisodes] = useState([])
  const [recentEpisode, setRecentEpisode] = useState()
  const [recentChecking, setRecentChecking] = useState(false)
  const [recentInfo, setRecentInfo] = useState(null)
  const [recentError, setRecentError] = useState('')
  const message = useMessage()
  const mode = Form.useWatch('mode', form) || 'exact'
  const distance = Form.useWatch(['options', 'distanceEnabled'], form)
  const cosine = Form.useWatch(['options', 'cosineEnabled'], form)
  const crossMode = Form.useWatch(['options', 'crossMode'], form)
  const markPosition = Form.useWatch(['options', 'markPosition'], form)

  useEffect(() => {
    let active = true
    api.get('/api/ui/config/pakku/settings').then(({ data }) => {
      if (active) { form.setFieldsValue({ ...data, options: editable(data.options) }); setLoaded(true) }
    }).catch(e => { if (active) setError(errorText(e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [form])

  const payload = async () => {
    await form.validateFields()
    const value = form.getFieldsValue(true)
    const { forceRulesText, whitelistText, ...options } = value.options
    return { enabled: value.enabled, mode: value.mode, windowSeconds: value.windowSeconds,
      latestCount: value.latestCount ?? 0,
      options: { ...options, forceRules: parseRules(forceRulesText),
        whitelist: (whitelistText || '').split('\n').map(x => x.trim()).filter(Boolean) } }
  }

  const save = async () => {
    setSaving(true); setError('')
    try {
      await api.put('/api/ui/config/pakku/settings', await payload())
      message.success('合并参数已完整保存，新请求立即生效')
    } catch (e) { if (!e.errorFields) setError(errorText(e)) }
    finally { setSaving(false) }
  }

  const tryPreview = async () => {
    setPreviewing(true); setPreviewError(''); setPreview(null)
    try {
      const comments = example.split('\n').filter(x => x.trim()).map((line, index) => {
        const [time, position, ...text] = line.split('|')
        const seconds = Number(time?.trim()), type = positions[position?.trim()]
        if (!time?.trim() || !Number.isFinite(seconds) || seconds < 0 || !type || !text.join('|').trim())
          throw Error(`第${index + 1}行格式不正确，示例：0 | 滚动 | 前方高能`)
        let sendtime = 0, id = String(index)
        if (text.length >= 3 && /^\d+$/.test(text.at(-1).trim()) && /^\d{10}$/.test(text.at(-2).trim())) {
          id = text.pop().trim(); sendtime = Number(text.pop().trim())
        }
        return { p: `${seconds},${type},25,16777215`, m: text.join('|').trim(), sendtime, id }
      })
      if (!comments.length || comments.length > 500) throw Error('请输入1—500条示例弹幕')
      const { data } = await api.post('/api/ui/config/pakku/preview', { ...await payload(), comments })
      setPreview(data)
    } catch (e) { if (!e.errorFields) setPreviewError(errorText(e)) }
    finally { setPreviewing(false) }
  }

  const loadRecentEpisodes = async () => {
    try {
      const {data} = await api.get('/api/ui/config/pakku/recent/episodes')
      setRecentEpisodes(data)
      if (!recentEpisode && data.length) setRecentEpisode(data[0].value)
    } catch (e) { setRecentError(errorText(e)) }
  }
  const checkRecent = async () => {
    if (!recentEpisode) { setRecentError('请先选择库内的B站分集'); return }
    setRecentChecking(true); setRecentError(''); setRecentInfo(null)
    try {
      const {data} = await api.post('/api/ui/config/pakku/recent/preview', {
        episodeId: recentEpisode, latestCount: form.getFieldValue('latestCount') || 3000,
      })
      setRecentInfo(data)
    } catch (e) { setRecentError(errorText(e)) }
    finally { setRecentChecking(false) }
  }

  const toggle = (key, label, hint, disabled = false) => <Form.Item key={key} name={['options', key]}
    label={label} tooltip={hint} valuePropName="checked"><Switch disabled={disabled} aria-label={label} /></Form.Item>
  const number = (key, label, min, max, hint, disabled = false) => <Form.Item key={key} name={['options', key]}
    label={label} tooltip={hint} rules={[{ required: true, type: 'integer', min, max }]}>
    <InputNumber min={min} max={max} disabled={disabled} aria-label={label} /></Form.Item>
  const choice = (key, label, options, hint, disabled = false) => <Form.Item key={key} name={['options', key]}
    label={label} tooltip={hint}><Select disabled={disabled} aria-label={label} options={options.map(([value, text]) => ({ value, label: text }))} /></Form.Item>
  const grid = children => <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-x-6">{children}</div>
  const smartOff = mode !== 'similar'
  const tabs = [
    { key: 'algorithm', label: '相似度与文字处理', children: <>
      {smartOff && <Alert type="info" showIcon className="mb-4" message="当前是仅相同内容模式。切到智能相似模式后，下面的相似度和文字处理参数才会生效。" />}
      {grid([
        toggle('distanceEnabled', '文字距离匹配', 'pakku按字符频次差异判断相似，不是严格的逐字编辑距离。', smartOff),
        number('maxDistance', '最大文字距离', 0, 20, '默认5。越小越严格；0仍可能合并字序不同的文字。仅相同内容请用精确模式。', smartOff || !distance),
        toggle('pinyin', '识别相同或相近拼音', '开启后，距离判断也会比较拼音。', smartOff || !distance),
        toggle('cosineEnabled', '余弦相似匹配', '使用pakku原生的字符二元组相似度。', smartOff),
        number('cosineThreshold', '余弦阈值', 1, 100, '默认45。数值越高越严格，与文字距离算法并行判断。', smartOff || !cosine),
        toggle('trimEnding', '忽略结尾标点', '如“前方高能”和“前方高能！”可被视为相同。', smartOff),
        toggle('trimSpace', '整理多余空格', '压缩连续空格，移除中文字符之间的空格。', smartOff),
        toggle('trimWidth', '统一全角与半角', '如全角数字６和半角数字6。', smartOff),
      ])}
    </> },
    { key: 'scope', label: '处理范围', children: grid([
      toggle('processScroll', '处理滚动及逆向弹幕', '关闭后，这些弹幕原样输出。'),
      toggle('processTop', '处理顶部弹幕', '关闭后，顶部弹幕原样输出。'),
      toggle('processBottom', '处理底部弹幕', '关闭后，底部弹幕原样输出。'),
      toggle('crossMode', '跨弹幕位置合并', '允许顶部、底部、滚动及逆向之间互相合并。'),
      toggle('modeElevation', '合并后尽量显示为固定弹幕', '需要跨弹幕位置合并。开启后优先底部，其次顶部；否则使用代表弹幕的位置。', !crossMode),
    ]) },
    { key: 'rules', label: '白名单与替换规则', children: <>
      <Form.Item name={['options', 'whitelistText']} label="不参与合并的白名单" tooltip="每行一个正则表达式，不区分大小写。命中的弹幕会保留为独立一条；精确与智能模式都生效。">
        <Input.TextArea rows={3} aria-label="不参与合并的白名单" placeholder="例如：^保护这条弹幕$" /></Form.Item>
      <Form.Item name={['options', 'forceRulesText']} label="合并前的替换规则（智能模式）"
        extra={'格式：[["正则表达式", "替换文本"]]；支持 $1 等分组引用。默认归一化233和666刷屏。'}
        rules={[{ validator: async (_, value) => { parseRules(value) } }]}>
        <Input.TextArea rows={6} aria-label="合并前的替换规则" disabled={smartOff} style={{ fontFamily: 'monospace' }} /></Form.Item>
      {grid([
        toggle('forceContinue', '继续匹配后续替换规则', '关闭时，命中第一条规则后就停止替换。', smartOff),
        toggle('forceApplySingles', '单条弹幕也显示替换结果', '关闭时，替换主要用于合并判断，未合并的单条保留原文。', smartOff),
      ])}
      <p className="text-sm text-gray-500">需要删除内容时，使用本页原有的“弹幕输出黑名单”。白名单只跳过重复合并，不能绕过输出黑名单。</p>
    </> },
    { key: 'display', label: '计数与代表弹幕', children: <>
      {grid([
        choice('markPosition', '计数位置', [['prefix', '文字前'], ['suffix', '文字后'], ['off', '不显示数字']], '只改变数字位置，不影响实际合并。'),
        choice('markStyle', '计数样式', [['brackets', '[10]'], ['pakku', '[x10]'], ['subscript', '₍₁₀₎']], '播放器不支持下标字符时可用普通方括号。', markPosition === 'off'),
        number('markMinCount', '显示数字的最少次数', 2, 10000, '达到此次数才显示数字；低于阈值仍可能已合并。', markPosition === 'off'),
        number('mergeMinCount', '执行合并的最少次数', 2, 10000, '组内总次数低于此值时，保留各条弹幕。已有来源计数在开启累加时参与计算。'),
        toggle('includeSourceCounts', '累加来源已有计数', '开启时“内容 X10”按10次累计；关闭时忽略历史次数，只统计本次返回的条目。'),
        number('representativePercent', '代表弹幕位置（百分比）', 0, 100, '决定采用组内哪一条的时间和属性：0为最早，50为中间，100为最后；pakku原版默认20。'),
        choice('textSelection', '合并后显示哪种文字', [['common', 'pakku选出的常见文本'], ['first', '组内最早的原文'], ['shortest', '最短原文'], ['longest', '最长原文']], '影响智能合并后的文字，不改变总次数。'),
      ])}
      <p className="text-sm text-gray-500">字号和密度相关选项见“字号与弹幕优选”页签。</p>
    </> },
    { key: 'density', label: '字号与弹幕优选', children: <>
      <Alert type="info" showIcon className="mb-4" message="Yamby：自动弹幕优选会直接减少返回条数。逐条字号是否显示，取决于Yamby是否读取字号信息，目前尚未确认；下方可预览计算结果。" />
      {toggle('enlarge', '弹幕合并后增大字号', '超过5次后，按pakku的对数公式放大。会累计已启用的来源计数；字号上限96。')}
      <Form.Item name={['options', 'shrinkThreshold']} label="自动减小字号" tooltip="按5秒窗口内的瞬时密度缩小字号，最大缩小约1.732倍。0关闭；阈值越低越强。"
        rules={[{ required: true, type: 'integer', min: 0, max: 1000 }]}><ThresholdInput label="自动减小字号阈值" /></Form.Item>
      <Form.Item name={['options', 'dropThreshold']} label="自动弹幕优选" tooltip="密度超出阈值时，优先舍弃低权重、未合并、低点赞弹幕。只影响输出，不删除原文件。0关闭。"
        rules={[{ required: true, type: 'integer', min: 0, max: 1000 }]}><ThresholdInput label="自动弹幕优选阈值" /></Form.Item>
      <p className="text-sm text-gray-600">与pakku相同，密度不是简单的每秒条数，还受文字长度、字号影响。重复次数和点赞越高，越容易在优选中保留。服务端使用固定抽样值，使相同输入和参数的结果可复现。</p>
      <p className="text-sm text-gray-500 mt-2">JSON接口保留原有参数顺序，并通过额外的fontSize字段提供字号；支持XML的播放器可在分集弹幕请求后加 ?format=xml 获取含字号的B站XML。现有Yamby接口地址保持不变。</p>
    </> },
  ]

  return <Card title="重复弹幕合并（pakku）" className="mt-4" loading={loading}>
    {error && <Alert type="error" showIcon message={error} className="mb-4" />}
    <Form form={form} layout="vertical" onValuesChange={() => setError('')} initialValues={{ enabled: false, mode: 'exact', windowSeconds: 30, options: editable(DEFAULTS) }}>
      <p className="text-sm text-gray-600 mb-4">短时间内重复弹幕合成一条，例如 [10] 前方高能。仅处理输出，原始弹幕文件保留。</p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6">
        <Form.Item name="enabled" label="启用重复合并" valuePropName="checked"><Switch aria-label="启用重复合并" /></Form.Item>
        <Form.Item name="mode" label="合并模式"><Select aria-label="重复弹幕合并模式" options={[
          { value: 'exact', label: '仅相同内容' }, { value: 'similar', label: '智能相似（pakku）' },
        ]} /></Form.Item>
        <Form.Item name="windowSeconds" label="时间范围（秒）" tooltip="同组最后一条与第一条的最大时间差，不会无限串联合并。"
          rules={[{ required: true, type: 'integer', min: 1, max: 120 }]}><InputNumber min={1} max={120} aria-label="重复弹幕合并时间范围" /></Form.Item>
      </div>
      <div className="border-t border-gray-200 py-4">
        <p className="font-semibold mb-2">按发送日期选择最新弹幕</p>
        <Form.Item name="latestCount" initialValue={0} label="最新发送条数"
          tooltip="先按观众发送日期选取最新N条，再执行原输出过滤和重复合并；合并后条数可能减少。0使用原来的全部输出。"
          rules={[{required: true, type: 'integer', min: 0, max: 1000000}]}><RecentCountInput /></Form.Item>
        <Alert type="info" showIcon className="mb-3" message="最新筛选目前支持B站来源，读取带发送日期的当前公开弹幕池，不修改原库。其他来源请选“全部”。最新N条不等于最近N天，也不保证覆盖平台全部历史弹幕。" />
        <Space wrap>
          <Select aria-label="核查最新弹幕的分集" style={{minWidth: 280}} showSearch optionFilterProp="label"
            placeholder="选择库内B站分集" options={recentEpisodes} value={recentEpisode}
            onFocus={loadRecentEpisodes} onChange={value => {setRecentEpisode(value); setRecentInfo(null)}} />
          <Button onClick={loadRecentEpisodes}>加载分集</Button>
          <Button loading={recentChecking} disabled={!loaded} onClick={checkRecent}>核查可获取数量（不保存）</Button>
        </Space>
        {recentError && <Alert type="error" showIcon className="mt-3" message={recentError} />}
        {recentInfo && <Alert type={recentInfo.partial ? 'warning' : 'success'} showIcon className="mt-3"
          message={`当前池去重后 ${recentInfo.availableCount} 条 → 选取最新 ${recentInfo.selectedCount} 条（目标 ${recentInfo.requestedCount}）；合并前数量。`}
          description={`${recentInfo.warning ? `${recentInfo.warning} ` : ''}${recentInfo.selectedCount < recentInfo.requestedCount ? '可获取数量不足目标，按实际条数返回。 ' : ''}选取范围：${displayDate(recentInfo.oldestSendTime)} 至 ${displayDate(recentInfo.newestSendTime)}。获取时间：${displayDate(recentInfo.fetchedAt)}；${recentInfo.partial ? '部分池不可用时缓存最多1分钟' : '缓存最多5分钟'}。`} />}
      </div>
      <Tabs items={tabs} />
      <div className="flex justify-between items-center flex-wrap gap-3 mb-5">
        <Space wrap><Button onClick={() => { form.setFieldsValue({ options: editable(DEFAULTS) }); setPreview(null); setError('') }}>恢复详细参数默认值</Button>
          <a href="https://github.com/dahaox3/pakku.js" target="_blank" rel="noreferrer">pakku 源码</a>
          <a href="/dist/pakku-integration-source.tar.gz" download>修改源码与许可证</a></Space>
        <Button type="primary" loading={saving} disabled={!loaded} onClick={save}>保存重复合并配置</Button>
      </div>
      <div className="border-t border-gray-200 pt-4">
        <p className="font-semibold mb-2">试算当前参数（无需保存）</p>
        <p className="text-sm text-gray-600 mb-3">每行：秒数 | 位置 | 内容。位置可填滚动、顶部、底部、逆向。可在末尾加“| 发送时间戳 | 弹幕ID”以试算最新筛选。只测试本区参数，不写入弹幕库；即使开关未启用也可预览。</p>
        <Input.TextArea aria-label="弹幕合并试算输入" rows={6} value={example} onChange={e => setExample(e.target.value)} />
        <Space className="my-3"><Button onClick={() => { setExample(EXAMPLE); setPreview(null) }}>填入示例</Button>
          <Button onClick={() => { setExample(denseExample()); setPreview(null) }}>填入密集测试</Button>
          <Button onClick={() => { setExample(recentExample()); setPreview(null) }}>填入新旧弹幕示例</Button>
          <Button loading={previewing} disabled={!loaded} onClick={tryPreview}>试算合并效果</Button></Space>
        {previewError && <Alert type="error" showIcon message={previewError} className="mb-3" />}
        {preview && <>
          <Alert type="success" showIcon className="mb-3" message={`输入 ${preview.inputCount} 条 → 输出 ${preview.outputCount} 条；最新筛选保留 ${preview.selectedCount ?? preview.inputCount} 条；含重复计数 ${preview.grouped} 组。结果对应点击试算时的参数。`} />
          <p className="text-sm text-gray-600 mb-3">放大 {preview.stats?.modified_enlarge || 0} 条，缩小 {preview.stats?.modified_shrink || 0} 条，优选舍弃 {preview.stats?.deleted_dispval || 0} 条；峰值密度 {Number(preview.stats?.num_max_dispval || 0).toFixed(1)}。</p>
          <Table size="small" pagination={{ pageSize: 8 }} rowKey={(_, index) => index}
            dataSource={preview.comments} columns={[
              { title: '时间（秒）', width: 100, render: (_, row) => row.p.split(',')[0] },
              { title: '位置', width: 80, render: (_, row) => Object.entries(positions).find(([, n]) => n === Number(row.p.split(',')[1]))?.[0] || '其他' },
              { title: '字号', width: 80, render: (_, row) => row.fontSize || 25 },
              { title: '合并后内容（字号示意）', dataIndex: 'm', render: (text, row) => <span style={{ fontSize: `${14 * (row.fontSize || 25) / 25}px`, overflowWrap: 'anywhere' }}>{text}</span> },
            ]} />
        </>}
      </div>
    </Form>
  </Card>
}
