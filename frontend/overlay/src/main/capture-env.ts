/**
 * Compute the environment the Swift AudioCapture child process should run under.
 *
 * Picks AUDIO_BACKEND_PORT from the parent env if set and non-empty; otherwise
 * falls back to AUDIO_TCP_PORT (the backend's bind-port variable); otherwise
 * defaults to 9090. Empty-string env vars (common when a user has the line in
 * `.env` but leaves the value blank) are treated as unset so they don't
 * propagate to the Swift child as empty strings.
 */
export function buildCaptureEnv(
  parentEnv: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv & { AUDIO_BACKEND_PORT: string } {
  const port =
    parentEnv.AUDIO_BACKEND_PORT || parentEnv.AUDIO_TCP_PORT || "9090";
  return {
    ...parentEnv,
    AUDIO_BACKEND_PORT: port,
  };
}
