# Mobile Store Submission Checklist

Use this before public release to the Apple App Store and Google Play.

## Accounts and access

- Expo account access with permission to build and manage credentials
- GitHub repository secret `EXPO_TOKEN` configured for mobile CI
- Apple Developer Program access
- App Store Connect app created for `com.montanablotter.app`
- Google Play Console app created for `com.montanablotter.app`
- Sentry project created and DSN issued for the mobile app

## Build validation

- `npm install`
- `npm run ci:verify`
- `npm run build:preview:all`
- Test the preview builds on at least one real iPhone and one real Android device
- Verify the app loads production data from `https://montanablotter.com`
- Verify a release build includes `EXPO_PUBLIC_SENTRY_DSN`
- Verify deep links and app launch do not regress after signing

## Store metadata

- App name: `Montana Blotter`
- Privacy policy URL: `https://montanablotter.com/privacy`
- Support email: `support@montanablotter.com`
- Support URL: `https://montanablotter.com/contact` if a public contact page exists, otherwise use the support email in store metadata
- Marketing description, keywords, and category prepared
- Age rating and content declarations completed
- Review notes prepared for any moderation-sensitive content

## Creative assets

- Final app icon exported for both stores
- Launch/splash screens checked on small and large devices
- iPhone screenshots captured from the current production build
- Android phone screenshots captured from the current production build
- Optional tablet screenshots prepared if tablet support is claimed

## Privacy and compliance

- Data collection and sharing answers in both stores match the live app behavior
- Privacy policy text reviewed against current app and backend behavior
- Terms and support links are live and reachable
- Any third-party SDK disclosures are included in store answers

## Crash reporting and observability

- `@sentry/react-native` configured with a production DSN
- Trigger a controlled test event before launch and confirm it appears in Sentry
  Use the hidden diagnostics screen in the Laws tab by tapping `Legal Disclaimer` seven times
- Decide whether to add `SENTRY_AUTH_TOKEN`, org, and project settings for source-map upload in CI/EAS
- Crash-free launch, feed load, post detail, and blog detail flows tested on real devices
- Basic release monitoring defined: install success, launch success, API error rate, crash rate
- A named owner is responsible for watching the first 72 hours after release

## Submission

- `npm run build:production:ios`
- `npm run build:production:android`
- `npm run submit:production:ios`
- `npm run submit:production:android`
- Confirm the exact submitted build numbers in App Store Connect and Play Console

## Post-release

- Smoke test the live store build after approval
- Confirm API traffic, error logging, and crash reporting are normal
- Document the released app version and store URLs
