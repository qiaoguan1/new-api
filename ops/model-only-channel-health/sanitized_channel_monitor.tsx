import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { SectionPageLayout } from '@/components/layout'
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

type PerfModel = {
  model_name: string
  avg_latency_ms: number
  success_rate: number
  avg_tps: number
  request_count: number
}

const number = (value?: number, digits = 0) =>
  new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value || 0)

export function ChannelMonitor() {
  const [hours, setHours] = useState('24')
  const [models, setModels] = useState<PerfModel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await api.get(`/api/perf-metrics/summary?hours=${hours}`)
      setModels(response.data?.data?.models || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载模型监控数据失败')
    } finally {
      setLoading(false)
    }
  }, [hours])

  useEffect(() => {
    load()
  }, [load])

  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>模型监控</SectionPageLayout.Title>
      <SectionPageLayout.Description>
        只展示模型名称和运行质量指标；数据按小时汇总，不展示渠道、上游、成本或毛利。
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
          <Card>
            <CardHeader>
              <CardTitle>模型性能（近 {hours} 小时）</CardTitle>
              <p className='text-sm text-muted-foreground'>每小时汇总，可手动刷新。</p>
            </CardHeader>
            <CardContent className='overflow-x-auto'>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>模型</TableHead>
                    <TableHead className='text-right'>请求数</TableHead>
                    <TableHead className='text-right'>成功率</TableHead>
                    <TableHead className='text-right'>平均延迟</TableHead>
                    <TableHead className='text-right'>输出速度</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {models.map((model) => (
                    <TableRow key={model.model_name}>
                      <TableCell className='font-medium'>{model.model_name}</TableCell>
                      <TableCell className='text-right'>{number(model.request_count)}</TableCell>
                      <TableCell className='text-right'>{number(model.success_rate, 2)}%</TableCell>
                      <TableCell className='text-right'>{number(model.avg_latency_ms)} ms</TableCell>
                      <TableCell className='text-right'>{number(model.avg_tps, 2)} token/s</TableCell>
                    </TableRow>
                  ))}
                  {!models.length && !loading && (
                    <TableRow>
                      <TableCell colSpan={5} className='h-24 text-center text-muted-foreground'>当前时间范围暂无模型调用数据</TableCell>
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
