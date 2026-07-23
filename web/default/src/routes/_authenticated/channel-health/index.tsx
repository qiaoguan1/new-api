import { createFileRoute } from '@tanstack/react-router'
import { ModelStatus } from '@/features/channel-monitor'

export const Route = createFileRoute('/_authenticated/channel-health/')({
  component: ModelStatus,
})
