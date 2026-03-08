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
```

## API endpoints expected

- `GET /api/posts`
- `GET /api/posts/:id`
- `GET /api/counties`
- `GET /api/agencies`
- `GET /api/stats`
- `GET /api/blog`
- `GET /api/blog/:slug`
