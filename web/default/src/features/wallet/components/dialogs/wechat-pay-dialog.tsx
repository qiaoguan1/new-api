import { useEffect, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { WechatPayOrder } from '../../types'

interface WechatPayDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  order: WechatPayOrder | null
}

export function WechatPayDialog({
  open,
  onOpenChange,
  order,
}: WechatPayDialogProps) {
  const { t } = useTranslation()
  const [now, setNow] = useState(() => Date.now())
  const amount = order ? (order.amount_cents / 100).toFixed(2) : '0.00'
  const remainingSeconds = order
    ? Math.max(0, order.expires_at - Math.floor(now / 1000))
    : 0
  const remainingTime = `${Math.floor(remainingSeconds / 60)
    .toString()
    .padStart(2, '0')}:${(remainingSeconds % 60).toString().padStart(2, '0')}`

  useEffect(() => {
    if (!open || !order) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [open, order])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='sm:max-w-md'>
        <DialogHeader>
          <DialogTitle>{t('WeChat Pay')}</DialogTitle>
          <DialogDescription>
            {t('Scan the QR code with WeChat to complete payment')}
          </DialogDescription>
        </DialogHeader>

        {order && (
          <div className='flex flex-col items-center gap-4 py-2'>
            <div className='rounded-xl bg-white p-4 ring-1 ring-black/10'>
              <QRCodeSVG value={order.code_url} size={220} level='M' />
            </div>
            <div className='w-full space-y-2 text-center'>
              <div className='text-2xl font-semibold'>¥{amount}</div>
              <div className='text-muted-foreground text-sm'>
                {remainingSeconds > 0
                  ? `${t('Payment code expires in')} ${remainingTime}`
                  : t('Payment code expired')}
              </div>
              <div className='text-muted-foreground text-xs break-all'>
                {t('Order number')}: {order.trade_no}
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant='outline' onClick={() => onOpenChange(false)}>
            {t('Close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
