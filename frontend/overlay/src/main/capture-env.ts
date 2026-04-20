/**
 * Compute the environment the Swift AudioCapture child process should run under.
 *
 * Picks AUDIO_BACKEND_PORT from the parent env if set; otherwise falls back to
 * AUDIO_TCP_PORT (the backend's bind-port variable); otherwise defaults to 9090.
 */
export function buildCaptureEnv(
  parentEnv: NodeJS.ProcessEnv,
): NodeJS.ProcessEnv {
  const port =
    parentEnv.AUDIO_BACKEND_PORT ?? parentEnv.AUDIO_TCP_PORT ?? "9090";
  return {
    ...parentEnv,
    AUDIO_BACKEND_PORT: port,
  };
}
