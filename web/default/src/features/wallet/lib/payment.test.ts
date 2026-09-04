/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'
import type { TopupInfo } from '../types'
import { getInitialTopupAmount } from './payment'

function createTopupInfo(amountOptions: number[]): TopupInfo {
  return {
    enable_online_topup: true,
    enable_stripe_topup: false,
    pay_methods: [],
    min_topup: 1,
    stripe_min_topup: 1,
    amount_options: amountOptions,
    discount: {},
  }
}

describe('initial top-up amount', () => {
  test('uses the first configured preset instead of the minimum amount', () => {
    const topupInfo = createTopupInfo([20, 50, 100])

    assert.equal(getInitialTopupAmount(topupInfo), 20)
  })

  test('falls back to the existing minimum when presets are unavailable', () => {
    const topupInfo = createTopupInfo([])

    assert.equal(getInitialTopupAmount(topupInfo), 1)
  })
})
