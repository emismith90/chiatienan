# Teams app package

Sideload-able Microsoft Teams app package for **chiatienan**.

## Build the package

1. Replace the placeholders in `manifest.json`:
   - `REPLACE_WITH_MICROSOFT_APP_ID` (two places: `id` and `bots[0].botId`) →
     your Entra bot registration's **Application (client) ID**.
   - `REPLACE_WITH_DOMAIN` → the public domain that serves `/api/messages`
     (the same `CADDY_DOMAIN` used at deploy time).
2. Zip the three files (the manifest and icons must be at the **root** of the zip):

   ```bash
   cd teams-app
   zip chiatienan.zip manifest.json color.png outline.png
   ```

3. In Teams: **Apps → Manage your apps → Upload a custom app** and pick
   `chiatienan.zip`, then add it to your lunch group chat. (Your Teams admin must
   allow custom-app sideloading — see the repo `README.md` §Prerequisites.)

## Icons

`color.png` (192×192) and `outline.png` (32×32) are plain placeholders — swap in
real artwork before a production rollout.
