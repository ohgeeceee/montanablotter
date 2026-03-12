# Montana Blotter Mobile App

Expo/React Native client for the Montana Blotter production API.

## Features

- Browse blotter posts with search and infinite scroll
- View individual post details
- Browse jail roster resources by county
- Browse Montana law summaries
- Browse blog posts and full blog article content

## Prerequisites

- Node.js 18+
- npm
- Expo CLI (`npx expo` is fine)

## Setup

1. Install dependencies:

```bash
npm install
```

2. Configure API base URL:

```bash
cp .env.example .env
```

Set `EXPO_PUBLIC_API_BASE_URL` in `.env`.

- Production: `https://montanablotter.com`
- Local backend from Android emulator: `http://10.0.2.2:5000`
- Local backend from iOS simulator: `http://127.0.0.1:5000`

## Run

```bash
npm run start
```

Then choose:

- `a` for Android
- `i` for iOS
- `w` for web

## Validation

```bash
npm run typecheck
npm run config
```

## Production release setup

This app is set up for Expo Application Services (EAS) builds.

### One-time setup

1. Install dependencies:

```bash
npm install
```

2. Log in to Expo:

```bash
npx eas login
```

3. Configure the production API in `.env`:

```bash
EXPO_PUBLIC_API_BASE_URL=https://montanablotter.com
EXPO_PUBLIC_SENTRY_DSN=your-sentry-dsn
EXPO_PUBLIC_SENTRY_ENVIRONMENT=production
```

4. Create the app records in the store dashboards using these identifiers:

- iOS bundle ID: `com.montanablotter.app`
- Android package: `com.montanablotter.app`

5. Verify Expo can read the final config:

```bash
npm run config
```

### Internal testing builds

Use internal builds before store submission.

```bash
npm run build:preview:ios
npm run build:preview:android
```

Or build both platforms together:

```bash
npm run build:preview:all
```

- iOS preview builds should be distributed through TestFlight.
- Android preview builds should go to the Play Console internal testing track.

### Production builds

Create store-ready binaries with:

```bash
npm run build:production:ios
npm run build:production:android
```

Submit them with:

```bash
npm run submit:production:ios
npm run submit:production:android
```

### Release checklist

- `npm run typecheck`
- `npm run config`
- Confirm `EXPO_PUBLIC_API_BASE_URL` points at production
- Confirm `EXPO_PUBLIC_SENTRY_DSN` is set for release builds
- Ensure GitHub Actions secret `EXPO_TOKEN` is set before relying on CI preview builds
- Upload final screenshots, privacy policy, support URL, and store metadata
- Verify Apple and Google signing credentials in EAS
- Test push-to-production builds on real devices before review submission
- Follow the detailed checklist in `STORE_SUBMISSION_CHECKLIST.md`

## Crash reporting

Crash reporting is wired through `@sentry/react-native`.

- Set `EXPO_PUBLIC_SENTRY_DSN` in `.env` for local release testing and in EAS project environment variables for hosted builds
- The app sends crash reports only when a DSN is present and the app is not running in dev mode
- API failures caused by network errors and server-side `5xx` responses are captured with endpoint metadata
- Query-string values are stripped from captured URLs before events are sent
- A hidden diagnostics screen can send test Sentry events from device builds: open the Laws tab and tap `Legal Disclaimer` seven times

Source map upload is not fully automated yet because it depends on Sentry org/project credentials and auth tokens.

## CI/CD

The repository includes a GitHub Actions workflow for the mobile app:

- Pull requests and pushes that touch `mobile/` run `npm run ci:verify`
- Pushes to `main` and manual workflow dispatches also trigger an EAS preview build

CI preview builds require the repository secret `EXPO_TOKEN`.

## API endpoints expected

- `GET /api/posts`
- `GET /api/posts/:id`
- `GET /api/counties`
- `GET /api/agencies`
- `GET /api/stats`
- `GET /api/blog`
- `GET /api/blog/:slug`
