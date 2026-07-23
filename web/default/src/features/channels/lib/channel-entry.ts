export type InitialChannelDialog = 'create-channel' | null

/** Maps a supported management entry action to the existing channels dialog. */
export function channelActionToInitialDialog(
  action: string | undefined
): InitialChannelDialog {
  return action === 'create' ? 'create-channel' : null
}
