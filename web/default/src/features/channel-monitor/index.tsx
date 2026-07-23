import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  CircleDollarSign,
  RefreshCw,
  Server,
  TriangleAlert,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import { api } from '@/lib/api'
import { SectionPageLayout } from '@/components/layout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

type DynamicPricing = {
  usd_per_1m_tokens_24h?: number
  total_tokens_24h?: number
}

type Upstream = {
  slug: string
  name: string
  health?: string
  health_label?: string
  channel_count?: number
  enabled_channels?: number
  calls_24h?: number
  success_24h?: number
  cost_24h_usd?: number
  avg_response_ms?: number
  dynamic_pricing_24h?: DynamicPricing
  last_error?: string
}

type BusinessChannel = {
  channel_id: number
  channel_name: string
  upstream_name: string
  group?: string
  calls: number
  success_calls: number
  error_calls: number
  success_rate?: number
  revenue_cny: number
  upstream_cost_cny: number
  gross_profit_cny: number
  gross_margin?: number
  cost_source?: string
  cost_confidence?: number
  latency_ms?: number
  models?: Array<{
    model: string
    calls: number
    revenue_cny: number
    upstream_cost_cny?: number
    gross_margin?: number
  }>
}

type BusinessModel = {
  upstream_slug: string
  upstream_name: string
  model: string
  kind?: string
  calls: number
  total_cost_cny: number
  input_cost_cny_per_m?: number
  cost_cny_per_image?: number
  allocated: boolean
}

type DailyBusiness = {
  date?: string
  generated_at_iso?: string
  totals?: {
    revenue_cny?: number
    upstream_cost_cny?: number
    allocated_cost_cny?: number
    unallocated_cost_cny?: number
    gross_profit_cny?: number
    gross_margin?: number
    calls?: number
    success_calls?: number
    error_calls?: number
    risk_channels?: number
  }
  channels?: BusinessChannel[]
  models?: BusinessModel[]
}

type MonitorData = {
  generated_at_iso?: string
  totals?: {
    upstreams?: number
    channels?: number
    enabled_channels?: number
    calls_24h?: number
    errors_24h?: number
    cost_24h_usd?: number
    cost_7d_usd?: number
    alerts?: number
  }
  upstreams?: Upstream[]
  daily_business?: DailyBusiness
}

type PerfModel = {
  model_name: string
  avg_latency_ms: number
  success_rate: number
  avg_tps: number
  request_count: number
}

const number = (value?: number, digits = 0) =>
  new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value || 0)
const money = (value?: number, digits = 4) => `¥${number(value, digits)}`
const percent = (value?: number) =>
  value === null || value === undefined ? '-' : `${number(value * 100, 2)}%`

function healthVariant(health?: string) {
  if (health === 'ok') return 'default' as const
  if (health === 'error' || health === 'down') return 'destructive' as const
  return 'secondary' as const
}

function marginClass(value?: number) {
  if (value === null || value === undefined) return 'text-muted-foreground'
  if (value < 0) return 'text-red-600'
  if (value < 0.2) return 'text-amber-600'
  return 'text-emerald-600'
}

export function ChannelMonitor() {
  const [hours, setHours] = useState('24')
  const [monitor, setMonitor] = useState<MonitorData | null>(null)
  const [models, setModels] = useState<PerfModel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [monitorRes, perfRes] = await Promise.all([
        api.get<MonitorData>('/api/channel-monitor'),
        api.get(`/api/perf-metrics/summary?hours=${hours}`),
      ])
      setMonitor(monitorRes.data)
      setModels(perfRes.data?.data?.models || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载渠道监控数据失败')
    } finally {
      setLoading(false)
    }
  }, [hours])

  useEffect(() => {
    load()
  }, [load])

  const totals = monitor?.totals || {}
  const business = monitor?.daily_business || {}
  const bt = business.totals || {}
  const successRate = totals.calls_24h
    ? ((totals.calls_24h - (totals.errors_24h || 0)) / totals.calls_24h) * 100
    : 0
  const runtimeRows = useMemo(
    () => [...(monitor?.upstreams || [])].sort((a, b) => (b.calls_24h || 0) - (a.calls_24h || 0)),
    [monitor]
  )
  const businessRows = business.channels || []
  const modelCosts = business.models || []

  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>渠道监控</SectionPageLayout.Title>
      <SectionPageLayout.Description>
        运行指标按滚动 24 小时更新；收入、上游真实成本和毛利按上一个完整 UTC 日核算，金额均为人民币。
      </SectionPageLayout.Description>
      <SectionPageLayout.Actions>
		<Select value={hours} onValueChange={(value) => setHours(value ?? '24')}>
          <SelectTrigger className='w-28'><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value='24'>近 24 小时</SelectItem>
            <SelectItem value='168'>近 7 天</SelectItem>
            <SelectItem value='720'>近 30 天</SelectItem>
          </SelectContent>
        </Select>
        <Button variant='outline' onClick={load} disabled={loading}>
          <RefreshCw className={loading ? 'animate-spin' : ''} />刷新
        </Button>
      </SectionPageLayout.Actions>
      <SectionPageLayout.Content>
        <div className='space-y-6'>
          {error && <div className='rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive'>{error}</div>}

          <div>
            <div className='mb-3 flex items-end justify-between gap-4'>
              <div>
                <h2 className='text-lg font-semibold'>完整日经营核算</h2>
                <p className='text-sm text-muted-foreground'>核算日期：{business.date || '等待数据'}；毛利已包含未归属上游成本。</p>
              </div>
              <span className='text-xs text-muted-foreground'>{business.generated_at_iso || ''}</span>
            </div>
            <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-6'>
              <MetricCard icon={WalletCards} title='站内消耗金额' value={money(bt.revenue_cny, 6)} detail={`${number(bt.calls)} 次调用`} />
              <MetricCard icon={CircleDollarSign} title='上游真实成本' value={money(bt.upstream_cost_cny, 6)} detail={`已归属 ${money(bt.allocated_cost_cny, 6)}`} />
              <MetricCard icon={TrendingUp} title='毛利额' value={money(bt.gross_profit_cny, 6)} detail='站内收入 - 全部上游成本' valueClass={marginClass(bt.gross_margin)} />
              <MetricCard icon={Activity} title='综合毛利率' value={percent(bt.gross_margin)} detail={`低毛利渠道 ${number(bt.risk_channels)} 个`} valueClass={marginClass(bt.gross_margin)} />
              <MetricCard icon={TriangleAlert} title='未归属成本' value={money(bt.unallocated_cost_cny, 6)} detail='无法匹配渠道/模型的成本' valueClass={(bt.unallocated_cost_cny || 0) > 0 ? 'text-red-600' : 'text-emerald-600'} />
              <MetricCard icon={Server} title='经营成功率' value={bt.calls ? `${number(((bt.success_calls || 0) / bt.calls) * 100, 2)}%` : '-'} detail={`${number(bt.error_calls)} 次错误`} />
            </div>
          </div>

          {(bt.unallocated_cost_cny || 0) > 0 && (
            <div className='rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700'>
              当前有 {money(bt.unallocated_cost_cny, 6)} 上游成本无法和 NewAPI 同日模型日志匹配。总毛利已扣除该成本，但不会强行分摊到具体渠道。
            </div>
          )}

          <Card>
            <CardHeader><CardTitle>渠道收入、真实成本与毛利（{business.date || '-'}）</CardTitle></CardHeader>
            <CardContent className='overflow-x-auto'>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>渠道 / 上游</TableHead><TableHead>模型</TableHead>
                  <TableHead className='text-right'>调用 / 成功率</TableHead>
                  <TableHead className='text-right'>站内消耗金额</TableHead>
                  <TableHead className='text-right'>上游真实成本</TableHead>
                  <TableHead className='text-right'>毛利额</TableHead>
                  <TableHead className='text-right'>毛利率</TableHead>
                  <TableHead>成本来源</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {businessRows.map((row) => (
                    <TableRow key={row.channel_id}>
                      <TableCell><div className='font-medium'>#{row.channel_id} {row.channel_name}</div><div className='text-xs text-muted-foreground'>{row.upstream_name} · {row.group || '-'}</div></TableCell>
                      <TableCell className='max-w-64 text-xs'>{(row.models || []).map(m => `${m.model}(${m.calls})`).join('、') || '-'}</TableCell>
                      <TableCell className='text-right'>{number(row.calls)}<div className='text-xs text-muted-foreground'>{number(row.success_rate, 2)}%</div></TableCell>
                      <TableCell className='text-right font-medium'>{money(row.revenue_cny, 6)}</TableCell>
                      <TableCell className='text-right'>{money(row.upstream_cost_cny, 6)}</TableCell>
                      <TableCell className={`text-right font-medium ${marginClass(row.gross_margin)}`}>{money(row.gross_profit_cny, 6)}</TableCell>
                      <TableCell className={`text-right font-semibold ${marginClass(row.gross_margin)}`}>{percent(row.gross_margin)}</TableCell>
                      <TableCell><Badge variant={row.cost_source === 'upstream_log' ? 'default' : 'secondary'}>{row.cost_source || '未知'}</Badge><div className='mt-1 text-xs text-muted-foreground'>置信度 {percent(row.cost_confidence)}</div></TableCell>
                    </TableRow>
                  ))}
                  {!businessRows.length && <TableRow><TableCell colSpan={8} className='h-24 text-center text-muted-foreground'>暂无完整日经营数据</TableCell></TableRow>}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>各上游模型真实成本</CardTitle></CardHeader>
            <CardContent className='overflow-x-auto'>
              <Table>
                <TableHeader><TableRow>
                  <TableHead>上游</TableHead><TableHead>模型</TableHead><TableHead>类型</TableHead>
                  <TableHead className='text-right'>上游调用</TableHead><TableHead className='text-right'>成本单价</TableHead>
                  <TableHead className='text-right'>成本合计</TableHead><TableHead>归属状态</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {modelCosts.map((row, index) => (
                    <TableRow key={`${row.upstream_slug}-${row.model}-${index}`}>
                      <TableCell className='font-medium'>{row.upstream_name}</TableCell>
                      <TableCell>{row.model}</TableCell><TableCell>{row.kind === 'image' ? '按次/图片/视频' : '文字 Token'}</TableCell>
                      <TableCell className='text-right'>{number(row.calls)}</TableCell>
                      <TableCell className='text-right'>{row.cost_cny_per_image !== null && row.cost_cny_per_image !== undefined ? `${money(row.cost_cny_per_image, 6)}/次` : `${money(row.input_cost_cny_per_m, 6)}/M 输入`}</TableCell>
                      <TableCell className='text-right font-medium'>{money(row.total_cost_cny, 6)}</TableCell>
                      <TableCell><Badge variant={row.allocated ? 'default' : 'destructive'}>{row.allocated ? '已归属渠道' : '未归属'}</Badge></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <div>
            <h2 className='mb-3 text-lg font-semibold'>滚动 24 小时运行监控</h2>
            <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-5'>
              <MetricCard icon={Activity} title='24h 成功率' value={`${number(successRate, 2)}%`} detail={`${number(totals.calls_24h)} 次调用 / ${number(totals.errors_24h)} 次错误`} />
              <MetricCard icon={WalletCards} title='24h 站内消耗' value={money(totals.cost_24h_usd, 6)} detail={`7 天 ${money(totals.cost_7d_usd, 6)}`} />
              <MetricCard icon={Server} title='渠道状态' value={`${number(totals.enabled_channels)} / ${number(totals.channels)}`} detail={`${number(totals.upstreams)} 个上游站点`} />
              <MetricCard icon={TriangleAlert} title='当前告警' value={number(totals.alerts)} detail='余额、错误率和响应速度' />
              <MetricCard icon={RefreshCw} title='更新时间' value={monitor?.generated_at_iso ? new Date(monitor.generated_at_iso).toLocaleTimeString('zh-CN') : '-'} detail='每 5 分钟刷新' />
            </div>
          </div>

          <Card>
            <CardHeader><CardTitle>上游运行排行</CardTitle></CardHeader>
            <CardContent className='overflow-x-auto'>
              <Table>
                <TableHeader><TableRow><TableHead>上游</TableHead><TableHead>状态</TableHead><TableHead className='text-right'>成功率</TableHead><TableHead className='text-right'>24h 调用</TableHead><TableHead className='text-right'>平均响应</TableHead><TableHead className='text-right'>站内消耗</TableHead><TableHead>最近错误</TableHead></TableRow></TableHeader>
                <TableBody>
                  {runtimeRows.map(row => {
                    const rate = row.calls_24h ? ((row.success_24h || 0) / row.calls_24h) * 100 : 0
                    return <TableRow key={row.slug}>
                      <TableCell><div className='font-medium'>{row.name}</div><div className='text-xs text-muted-foreground'>{number(row.enabled_channels)} / {number(row.channel_count)} 通道启用</div></TableCell>
                      <TableCell><Badge variant={healthVariant(row.health)}>{row.health_label || row.health || '未知'}</Badge></TableCell>
                      <TableCell className='text-right'>{number(rate, 2)}%</TableCell><TableCell className='text-right'>{number(row.calls_24h)}</TableCell>
                      <TableCell className='text-right'>{row.avg_response_ms ? `${number(row.avg_response_ms)} ms` : '-'}</TableCell>
                      <TableCell className='text-right'>{money(row.cost_24h_usd, 6)}</TableCell>
                      <TableCell className='max-w-72 truncate text-xs text-muted-foreground' title={row.last_error}>{row.last_error || '-'}</TableCell>
                    </TableRow>
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>模型性能（近 {hours} 小时）</CardTitle></CardHeader>
            <CardContent className='overflow-x-auto'>
              <Table><TableHeader><TableRow><TableHead>模型</TableHead><TableHead className='text-right'>请求数</TableHead><TableHead className='text-right'>成功率</TableHead><TableHead className='text-right'>平均延迟</TableHead><TableHead className='text-right'>输出速度</TableHead></TableRow></TableHeader>
                <TableBody>{models.map(model => <TableRow key={model.model_name}><TableCell className='font-medium'>{model.model_name}</TableCell><TableCell className='text-right'>{number(model.request_count)}</TableCell><TableCell className='text-right'>{number(model.success_rate, 2)}%</TableCell><TableCell className='text-right'>{number(model.avg_latency_ms)} ms</TableCell><TableCell className='text-right'>{number(model.avg_tps, 2)} token/s</TableCell></TableRow>)}</TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}

function MetricCard({ icon: Icon, title, value, detail, valueClass = '' }: { icon: typeof Activity; title: string; value: string; detail: string; valueClass?: string }) {
  return <Card><CardHeader className='pb-2'><CardTitle className='flex items-center gap-2 text-sm font-medium'><Icon className='size-4' />{title}</CardTitle></CardHeader><CardContent><div className={`text-2xl font-semibold ${valueClass}`}>{value}</div><p className='text-xs text-muted-foreground'>{detail}</p></CardContent></Card>
}
