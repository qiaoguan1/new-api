import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import {
  getOAuthSessionStorage,
  markAndNavigateOAuthBindPopup,
  markOAuthBindPopup,
  resolveOAuthCallbackMode,
  type OAuthModeStorage,
} from '../src/features/auth/lib/oauth-callback-mode'

function fakeStorage(initial: Record<string, string> = {}): OAuthModeStorage {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
  }
}

const openOpener = { closed: false }
const bindState = 'bind-state'

describe('resolveOAuthCallbackMode', () => {
  test('matching provider and state mark is treated as a bind flow', () => {
    const storage = fakeStorage()
    assert.equal(markOAuthBindPopup(storage, 'oidc', bindState), true)
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: openOpener,
        storage,
      }),
      'bind'
    )
  })

  test('login redirect in a tab with a foreign opener stays a login flow', () => {
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: openOpener,
        storage: fakeStorage(),
      }),
      'login'
    )
  })

  test('provider and state mismatches stay login flows', () => {
    const storage = fakeStorage()
    markOAuthBindPopup(storage, 'github', bindState)
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: openOpener,
        storage,
      }),
      'login'
    )

    markOAuthBindPopup(storage, 'oidc', 'previous-state')
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: openOpener,
        storage,
      }),
      'login'
    )
  })

  test('missing or closed opener stays a login flow', () => {
    const storage = fakeStorage()
    markOAuthBindPopup(storage, 'oidc', bindState)
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: null,
        storage,
      }),
      'login'
    )
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: { closed: true },
        storage,
      }),
      'login'
    )
  })

  test('blocked storage degrades to login without throwing', () => {
    const storage: OAuthModeStorage = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => undefined,
    }
    assert.equal(
      resolveOAuthCallbackMode('oidc', bindState, {
        opener: openOpener,
        storage,
      }),
      'login'
    )
  })
})

describe('OAuth bind popup storage', () => {
  test('blocked sessionStorage getter is contained', () => {
    const owner = {
      get sessionStorage(): OAuthModeStorage {
        throw new Error('blocked')
      },
    }
    assert.equal(getOAuthSessionStorage(owner), null)
  })

  test('marking reports unavailable or unwritable storage', () => {
    const storage: OAuthModeStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error('blocked')
      },
    }
    assert.equal(markOAuthBindPopup(null, 'oidc', bindState), false)
    assert.equal(markOAuthBindPopup(storage, 'oidc', bindState), false)
  })

  test('successful navigation writes the exact marker before replacing location', () => {
    const events: string[] = []
    const data = new Map<string, string>()
    const popup = {
      closed: false,
      sessionStorage: {
        getItem: (key: string) => data.get(key) ?? null,
        setItem: (key: string, value: string) => {
          events.push(`set:${key}:${value}`)
          data.set(key, value)
        },
      },
      location: {
        replace: (url: string) => events.push(`navigate:${url}`),
      },
    }

    assert.equal(
      markAndNavigateOAuthBindPopup(
        popup,
        'oidc',
        bindState,
        'https://provider.example/authorize'
      ),
      true
    )
    assert.deepEqual(events, [
      `set:oauth_bind_flow:oidc:${bindState}`,
      'navigate:https://provider.example/authorize',
    ])
  })

  test('closed popup or blocked storage never navigates', () => {
    let navigated = false
    const location = { replace: () => void (navigated = true) }
    assert.equal(
      markAndNavigateOAuthBindPopup(
        {
          closed: true,
          sessionStorage: fakeStorage(),
          location,
        },
        'oidc',
        bindState,
        'https://provider.example/authorize'
      ),
      false
    )
    assert.equal(
      markAndNavigateOAuthBindPopup(
        {
          closed: false,
          get sessionStorage(): OAuthModeStorage {
            throw new Error('blocked')
          },
          location,
        },
        'oidc',
        bindState,
        'https://provider.example/authorize'
      ),
      false
    )
    assert.equal(navigated, false)
  })
})
