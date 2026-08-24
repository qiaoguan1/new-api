const OAUTH_BIND_FLOW_KEY_PREFIX = 'oauth_bind_flow:'

/** Minimal sessionStorage contract used by browser code and deterministic tests. */
export interface OAuthModeStorage {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

/** Browser object that owns popup-scoped sessionStorage. */
export interface OAuthSessionStorageOwner {
  readonly sessionStorage: OAuthModeStorage
}

/** Minimal opener state needed to reject closed callback targets. */
export interface OAuthModeOpener {
  closed: boolean
}

/** Same-origin popup capabilities used before navigating to an OAuth provider. */
export interface OAuthBindPopup
  extends OAuthModeOpener,
    OAuthSessionStorageOwner {
  location: { replace: (url: string) => void }
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

/** Mark the popup before provider navigation and fail closed on any browser error. */
export function markAndNavigateOAuthBindPopup(
  popup: OAuthBindPopup,
  provider: string,
  state: string,
  url: string
): boolean {
  if (popup.closed) return false
  if (!markOAuthBindPopup(getOAuthSessionStorage(popup), provider, state)) {
    return false
  }
  try {
    popup.location.replace(url)
    return true
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

  let markedState: string | null
  try {
    markedState = context.storage.getItem(
      `${OAUTH_BIND_FLOW_KEY_PREFIX}${provider}`
    )
  } catch {
    return 'login'
  }

  return markedState === state ? 'bind' : 'login'
}
