import { describe, expect, it } from "vitest";
import { narrativeDraftAfterSettingsLoad, narrativeSettingsDiffer } from "./ProgressPanel";
import type { SettingsView } from "./types";

const saved = {
  enabled: true,
  automatic: false,
  provider_id: "provider-1",
  cap_usd: 5,
  max_requests: 10,
} as SettingsView["settings"];

describe("narrative settings draft hydration", () => {
  it("fills an empty draft from loaded settings", () => {
    expect(narrativeDraftAfterSettingsLoad(saved, null, false)).toBe(saved);
  });

  it("preserves changed draft values when settings refresh after the panel closes", () => {
    const edited = { ...saved, enabled: false };
    expect(narrativeDraftAfterSettingsLoad(saved, edited, true)).toBe(edited);
  });

  it("refreshes an unchanged draft from newly saved settings", () => {
    const refreshed = { ...saved, cap_usd: 2 };
    expect(narrativeDraftAfterSettingsLoad(refreshed, { ...refreshed }, false)).toBe(refreshed);
  });

  it("tracks whether the draft differs from the last saved settings", () => {
    expect(narrativeSettingsDiffer({ ...saved, cap_usd: 2 }, saved)).toBe(true);
    expect(narrativeSettingsDiffer({ ...saved }, saved)).toBe(false);
  });
});
