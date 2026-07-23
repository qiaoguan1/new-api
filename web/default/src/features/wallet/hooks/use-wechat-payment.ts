import { useCallback, useEffect, useRef, useState } from 'react'
import i18next from 'i18next'
import { toast } from 'sonner'
import { getWechatPayOrder, isApiSuccess, requestWechatPayNative } from '../api'
import type { WechatPayOrder } from '../types'

const POLL_INTERVAL_MS = 2000
const MAX_POLL_DURATION_MS = 5 * 60 * 1000

export function useWechatPayment(onSuccess?: () => void | Promise<void>) {
  const [order, setOrder] = useState<WechatPayOrder | null>(null)
  const [processing, setProcessing] = useState(false)
  const pollTimerRef = useRef<number | null>(null)
  const pollStartedAtRef = useRef(0)
  const pollingRef = useRef(false)
  const onSuccessRef = useRef(onSuccess)

  useEffect(() => {
    onSuccessRef.current = onSuccess
  }, [onSuccess])

  const stopPolling = useCallback(() => {
    pollingRef.current = false
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const close = useCallback(() => {
    stopPolling()
    setOrder(null)
  }, [stopPolling])

  const schedulePoll = useCallback(
    (tradeNo: string, expiresAt: number) => {
      stopPolling()
      pollingRef.current = true
      pollStartedAtRef.current = Date.now()
      const pollDeadline = Math.min(
        pollStartedAtRef.current + MAX_POLL_DURATION_MS,
        expiresAt * 1000
      )

      const poll = async () => {
        if (!pollingRef.current) return
        if (Date.now() >= pollDeadline) {
          stopPolling()
          toast.error(i18next.t('Payment polling timed out'))
          return
        }

        try {
          const response = await getWechatPayOrder(tradeNo)
          if (!pollingRef.current) return
          if (
            isApiSuccess(response) &&
            response.data?.local_status === 'success'
          ) {
            stopPolling()
            toast.success(i18next.t('Payment successful'))
            await onSuccessRef.current?.()
            setOrder(null)
            return
          }
        } catch {
          // Keep polling until the order expires or the dialog is closed.
        }

        if (pollingRef.current) {
          pollTimerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS)
        }
      }

      pollTimerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS)
    },
    [stopPolling]
  )

  const createOrder = useCallback(
    async (topupAmount: number) => {
      try {
        setProcessing(true)
        const response = await requestWechatPayNative({
          amount: Math.floor(topupAmount),
        })
        if (!isApiSuccess(response) || !response.data?.code_url) {
          toast.error(response.message || i18next.t('Payment request failed'))
          return false
        }
        setOrder(response.data)
        schedulePoll(response.data.trade_no, response.data.expires_at)
        return true
      } catch {
        toast.error(i18next.t('Payment request failed'))
        return false
      } finally {
        setProcessing(false)
      }
    },
    [schedulePoll]
  )

  useEffect(() => stopPolling, [stopPolling])

  return {
    order,
    open: order !== null,
    processing,
    createOrder,
    close,
  }
}
