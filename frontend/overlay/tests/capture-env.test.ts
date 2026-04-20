import { describe, it, expect } from "vitest";
import { buildCaptureEnv } from "../src/main/capture-env";

describe("buildCaptureEnv", () => {
  it("forwards AUDIO_BACKEND_PORT from the parent env", () => {
    const env = buildCaptureEnv({ AUDIO_BACKEND_PORT: "9191" });
    expect(env.AUDIO_BACKEND_PORT).toBe("9191");
  });

  it("defaults AUDIO_BACKEND_PORT to 9090 when unset", () => {
    const env = buildCaptureEnv({});
    expect(env.AUDIO_BACKEND_PORT).toBe("9090");
  });

  it("coerces numeric AUDIO_TCP_PORT when AUDIO_BACKEND_PORT is absent", () => {
    const env = buildCaptureEnv({ AUDIO_TCP_PORT: "9292" });
    expect(env.AUDIO_BACKEND_PORT).toBe("9292");
  });

  it("AUDIO_BACKEND_PORT wins over AUDIO_TCP_PORT when both are set", () => {
    const env = buildCaptureEnv({
      AUDIO_BACKEND_PORT: "1111",
      AUDIO_TCP_PORT: "2222",
    });
    expect(env.AUDIO_BACKEND_PORT).toBe("1111");
  });

  it("preserves unrelated env vars", () => {
    const env = buildCaptureEnv({ PATH: "/usr/bin", HOME: "/tmp" });
    expect(env.PATH).toBe("/usr/bin");
    expect(env.HOME).toBe("/tmp");
  });

  it("treats empty-string AUDIO_BACKEND_PORT as unset and falls through", () => {
    const env = buildCaptureEnv({
      AUDIO_BACKEND_PORT: "",
      AUDIO_TCP_PORT: "9393",
    });
    expect(env.AUDIO_BACKEND_PORT).toBe("9393");
  });

  it("treats empty-string AUDIO_TCP_PORT as unset and defaults to 9090", () => {
    const env = buildCaptureEnv({ AUDIO_TCP_PORT: "" });
    expect(env.AUDIO_BACKEND_PORT).toBe("9090");
  });
});
