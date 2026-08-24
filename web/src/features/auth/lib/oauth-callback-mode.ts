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
/**
 * Distinguishes an account-bind callback from a normal OAuth login callback.
 * A live `window.opener` is not proof of a bind because tabs opened by other
 * sites retain their opener across an identity-provider round trip. Bind
 * popups therefore carry an exact provider/state marker in their own
 * sessionStorage before leaving the application's origin.
 */
const OAUTH_BIND_FLOW_KEY_PREFIX = 'oauth_bind_flow:'

/** Minimal sessionStorage contract used by browser code and deterministic tests. */
export interface OAuthModeStorage {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

/** Browser object that owns the popup-scoped sessionStorage. */
export interface OAuthSessionStorageOwner {
  readonly sessionStorage: OAuthModeStorage
}

/** Minimal opener state needed to reject closed callback targets. */
export interface OAuthModeOpener {
  closed: boolean
}

/** Evidence available when classifying an OAuth callback. */
export interface OAuthCallbackModeContext {
  opener: OAuthModeOpener | null | undefined
  storage: OAuthModeStorage | null | undefined
}

export type OAuthCallbackMode = 'login' | 'bind'

/** Access sessionStorage without letting browser privacy controls crash OAuth. */
export function getOAuthSessionStorage(
  owner: OAuthSessionStorageOwner | null | undefined
): OAuthModeStorage | null {
  try {
    return owner?.sessionStorage ?? null
  } catch {
    return null
  }
}

/** Mark a same-origin popup as the bind flow for one exact provider and state. */
export function markOAuthBindPopup(
  storage: OAuthModeStorage | null | undefined,
  provider: string,
  state: string
): boolean {
  if (!storage || !provider || !state) return false

  try {
    const key = `${OAUTH_BIND_FLOW_KEY_PREFIX}${provider}`
    storage.setItem(key, state)
    return storage.getItem(key) === state
  } catch {
    return false
  }
}

/** Resolve to bind only with a live opener and an exact popup-scoped marker. */
export function resolveOAuthCallbackMode(
  provider: string,
  state: string,
  context: OAuthCallbackModeContext
): OAuthCallbackMode {
  if (!context.opener || context.opener.closed || !context.storage || !state) {
    return 'login'
  }

  let markedState: string | null = null
  try {
    markedState = context.storage.getItem(
      `${OAUTH_BIND_FLOW_KEY_PREFIX}${provider}`
    )
  } catch {
    return 'login'
  }

  return markedState === state ? 'bind' : 'login'
}
