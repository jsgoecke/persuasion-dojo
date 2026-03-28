/**
 * electron-builder afterSign hook — notarizes the .app before it is wrapped
 * into the distribution .dmg.
 *
 * Called automatically by electron-builder during `npm run package` when
 * building on macOS. Skips gracefully in CI without credentials and in
 * local dev (APPLE_ID absent).
 *
 * Required environment variables (set as GitHub Actions secrets):
 *   APPLE_ID                  — Apple ID email used for notarization
 *   APPLE_APP_SPECIFIC_PASSWORD — app-specific password from appleid.apple.com
 *   APPLE_TEAM_ID             — 10-character team ID from developer.apple.com
 *
 * Notarization uses notarytool (requires macOS 12+ on the build machine).
 * xcrun notarytool replaced altool in late 2023.
 */
"use strict";

const { notarize } = require("@electron/notarize");

/** @param {import("electron-builder").AfterPackContext} context */
exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;

  if (electronPlatformName !== "darwin") {
    return;
  }

  const appleId = process.env.APPLE_ID;
  if (!appleId) {
    console.warn(
      "[notarize] APPLE_ID not set — skipping notarization (local dev or CI without secrets)",
    );
    return;
  }

  const appleIdPassword = process.env.APPLE_APP_SPECIFIC_PASSWORD;
  const teamId = process.env.APPLE_TEAM_ID;

  if (!appleIdPassword || !teamId) {
    throw new Error(
      "[notarize] APPLE_APP_SPECIFIC_PASSWORD and APPLE_TEAM_ID must both be set when APPLE_ID is present",
    );
  }

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;

  console.log(`[notarize] Submitting ${appName}.app to Apple notary service…`);
  console.log(`[notarize]   appBundleId: com.persuasiondojo.overlay`);
  console.log(`[notarize]   teamId:      ${teamId}`);
  console.log(`[notarize]   appPath:     ${appPath}`);

  await notarize({
    tool: "notarytool",
    appBundleId: "com.persuasiondojo.overlay",
    appPath,
    appleId,
    appleIdPassword,
    teamId,
  });

  console.log(`[notarize] ✓ ${appName}.app notarized successfully`);
};
