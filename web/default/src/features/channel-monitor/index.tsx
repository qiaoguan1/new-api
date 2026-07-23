import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Boxes,
  CircleCheckBig,
  RefreshCw,
  Timer,
  Zap,
} from 'lucide-react'
import { api } from '@/lib/api'
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
import { SectionPageLayout } from '@/components/layout'

type ModelPerformance = {
  model_name: string
  avg_latency_ms: number
  success_rate: number
  avg_tps: number
  request_count: number
}

type ModelPerformanceResponse = {
  success: boolean
  data?: {
    models?: ModelPerformance[]
  }
}

const number = (value?: number, digits = 0) =>
  new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(
    value || 0
  )

function statusFor(successRate: number) {
  if (successRate >= 99) {
    return { label: '稳定', variant: 'default' as const }
  }
  if (successRate >= 90) {
    return { label: '波动', variant: 'secondary' as const }
  }
  return { label: '异常', variant: 'destructive' as const }
}

function labelForRange(hours: string) {
  if (hours === '168') return '近 7 天'
  if (hours === '720') return '近 30 天'
  return '近 24 小时'
}

export function ModelStatus() {
  const [hours, setHours] = useState('24')
  const [models, setModels] = useState<ModelPerformance[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const rangeLabel = labelForRange(hours)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.get<ModelPerformanceResponse>(
        `/api/perf-metrics/summary?hours=${hours}`
      )
      setModels(response.data?.data?.models || [])
      setUpdatedAt(new Date())
    } catch {
      setError('模型状态暂时无法加载，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [hours])

  useEffect(() => {
    load()
  }, [load])

  const sortedModels = useMemo(
    () => [...models].sort((a, b) => b.request_count - a.request_count),
    [models]
  )

  const summary = useMemo(() => {
    const requests = models.reduce((sum, model) => sum + model.request_count, 0)
    const successfulRequests = models.reduce(
      (sum, model) => sum + model.request_count * (model.success_rate / 100),
      0
    )
    const totalLatency = models.reduce(
      (sum, model) => sum + model.avg_latency_ms * model.request_count,
      0
    )

    return {
      requests,
      successRate: requests ? (successfulRequests / requests) * 100 : 0,
      avgLatency: requests ? totalLatency / requests : 0,
    }
  }, [models])

  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>模型状态</SectionPageLayout.Title>
      <SectionPageLayout.Description>
        根据真实请求汇总模型可用性与性能，帮助你选择更稳定的模型。
      </SectionPageLayout.Description>
      <SectionPageLayout.Actions>
        <Select
          value={hours}
          onValueChange={(value) => setHours(value ?? '24')}
        >
          <SelectTrigger className='w-28'>
            <SelectValue>{rangeLabel}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='24'>近 24 小时</SelectItem>
            <SelectItem value='168'>近 7 天</SelectItem>
            <SelectItem value='720'>近 30 天</SelectItem>
          </SelectContent>
        </Select>
        <Button variant='outline' onClick={load} disabled={loading}>
          <RefreshCw className={loading ? 'animate-spin' : ''} />
          刷新
        </Button>
      </SectionPageLayout.Actions>
      <SectionPageLayout.Content>
        <div className='space-y-6'>
          {error && (
            <div className='border-destructive/40 bg-destructive/5 text-destructive rounded-md border p-3 text-sm'>
              {error}
            </div>
          )}

          <div className='grid gap-4 md:grid-cols-2 xl:grid-cols-4'>
            <MetricCard
              icon={Boxes}
              title='活跃模型'
              value={number(models.length)}
              detail={`${rangeLabel}有真实请求`}
            />
            <MetricCard
              icon={Activity}
              title='请求总数'
              value={number(summary.requests)}
              detail={`${rangeLabel}累计`}
            />
            <MetricCard
              icon={CircleCheckBig}
              title='整体成功率'
              value={
                summary.requests ? `${number(summary.successRate, 2)}%` : '-'
              }
              detail='按请求数量加权'
            />
            <MetricCard
              icon={Timer}
              title='平均响应时间'
              value={
                summary.requests ? `${number(summary.avgLatency)} ms` : '-'
              }
              detail={
                updatedAt
                  ? `更新于 ${updatedAt.toLocaleTimeString('zh-CN')}`
                  : '等待更新'
              }
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>模型性能（{rangeLabel}）</CardTitle>
            </CardHeader>
            <CardContent className='overflow-x-auto'>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>模型</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead className='text-right'>请求数</TableHead>
                    <TableHead className='text-right'>成功率</TableHead>
                    <TableHead className='text-right'>平均响应</TableHead>
                    <TableHead className='text-right'>输出速度</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedModels.map((model) => {
                    const status = statusFor(model.success_rate)
                    return (
                      <TableRow key={model.model_name}>
                        <TableCell className='font-medium'>
                          {model.model_name}
                        </TableCell>
                        <TableCell>
                          <Badge variant={status.variant}>{status.label}</Badge>
                        </TableCell>
                        <TableCell className='text-right'>
                          {number(model.request_count)}
                        </TableCell>
                        <TableCell className='text-right'>
                          {number(model.success_rate, 2)}%
                        </TableCell>
                        <TableCell className='text-right'>
                          {number(model.avg_latency_ms)} ms
                        </TableCell>
                        <TableCell className='text-right'>
                          <span className='inline-flex items-center justify-end gap-1'>
                            <Zap className='size-3.5' />
                            {model.avg_tps > 0
                              ? `${number(model.avg_tps, 2)} token/s`
                              : '-'}
                          </span>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                  {!loading && !sortedModels.length && (
                    <TableRow>
                      <TableCell
                        colSpan={6}
                        className='text-muted-foreground h-24 text-center'
                      >
                        所选时间范围内暂无模型请求数据
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}

function MetricCard({
  icon: Icon,
  title,
  value,
  detail,
}: {
  icon: typeof Activity
  title: string
  value: string
  detail: string
}) {
  return (
    <Card>
      <CardHeader className='pb-2'>
        <CardTitle className='flex items-center gap-2 text-sm font-medium'>
          <Icon className='size-4' />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className='text-2xl font-semibold'>{value}</div>
        <p className='text-muted-foreground text-xs'>{detail}</p>
      </CardContent>
    </Card>
  )
}
