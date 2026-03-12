import { Platform } from 'react-native';
import * as Sentry from '@sentry/react-native';

const sentryDsn = process.env.EXPO_PUBLIC_SENTRY_DSN?.trim();
const sentryEnvironment = process.env.EXPO_PUBLIC_SENTRY_ENVIRONMENT?.trim() || (__DEV__ ? 'development' : 'production');

let hasInitialized = false;

function redactUrl(rawUrl: string): string {
  try {
    const url = new URL(rawUrl);
    url.search = '';
    return url.toString();
  } catch {
    const [path] = rawUrl.split('?');
    return path;
  }
}

function redactBreadcrumbData(data: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!data) {
    return data;
  }

  const nextData = { ...data };
  const url = typeof nextData.url === 'string' ? nextData.url : undefined;

  if (url) {
    nextData.url = redactUrl(url);
  }

  return nextData;
}

export function initMonitoring() {
  if (hasInitialized || !sentryDsn) {
    return;
  }

  Sentry.init({
    dsn: sentryDsn,
    enabled: !__DEV__,
    environment: sentryEnvironment,
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
    enableAutoSessionTracking: true,
    enableWatchdogTerminationTracking: true,
    beforeBreadcrumb(breadcrumb) {
      return {
        ...breadcrumb,
        data: redactBreadcrumbData(breadcrumb.data),
      };
    },
    beforeSend(event) {
      const nextEvent = { ...event };

      if (nextEvent.request?.url) {
        nextEvent.request = {
          ...nextEvent.request,
          url: redactUrl(nextEvent.request.url),
        };
      }

      if (nextEvent.breadcrumbs) {
        nextEvent.breadcrumbs = nextEvent.breadcrumbs.map((breadcrumb) => ({
          ...breadcrumb,
          data: redactBreadcrumbData(breadcrumb.data),
        }));
      }

      return nextEvent;
    },
  });

  Sentry.setTags({
    app_platform: Platform.OS,
    app_runtime: 'expo',
  });

  hasInitialized = true;
}

export function getMonitoringStatus() {
  return {
    dsnConfigured: Boolean(sentryDsn),
    environment: sentryEnvironment,
    enabledInCurrentBuild: Boolean(sentryDsn) && !__DEV__,
  };
}

export async function sendTestMonitoringEvent(kind: 'message' | 'exception') {
  if (!sentryDsn) {
    throw new Error('Sentry DSN is not configured for this build.');
  }

  const timestamp = new Date().toISOString();

  if (kind === 'message') {
    Sentry.withScope((scope) => {
      scope.setTag('diagnostic_event', 'true');
      scope.setLevel('info');
      Sentry.captureMessage(`Mobile diagnostics message ${timestamp}`);
    });
  } else {
    Sentry.withScope((scope) => {
      scope.setTag('diagnostic_event', 'true');
      scope.setLevel('error');
      Sentry.captureException(new Error(`Mobile diagnostics exception ${timestamp}`));
    });
  }

  await Sentry.flush();
}

export function captureApiFailure(error: unknown, context: {
  endpoint: string;
  method: string;
  status?: number;
  queryKeys?: string[];
}) {
  if (!sentryDsn || __DEV__) {
    return;
  }

  Sentry.withScope((scope) => {
    scope.setTag('error_source', 'api');
    scope.setTag('http_method', context.method);
    scope.setTag('api_endpoint', context.endpoint);

    if (typeof context.status === 'number') {
      scope.setExtra('status', context.status);
    }

    if (context.queryKeys?.length) {
      scope.setExtra('query_keys', context.queryKeys);
    }

    Sentry.captureException(error);
  });
}
