import assert from 'node:assert/strict'
import test from 'node:test'
import { channelActionToInitialDialog } from '../src/features/channels/lib/channel-entry.ts'


test('create action opens the existing create-channel dialog', () => {
  assert.equal(channelActionToInitialDialog('create'), 'create-channel')
})

test('missing or unsupported actions do not open a dialog', () => {
  assert.equal(channelActionToInitialDialog(undefined), null)
  assert.equal(channelActionToInitialDialog('delete'), null)
})
