import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';
import { Subscription } from 'expo-modules-core';
import { fetchAPI } from './api';
import { getToken } from './auth';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

async function getExpoPushToken(): Promise<string | null> {
  if (!Device.isDevice) {
    return null;
  }

  const existingStatus = (await Notifications.getPermissionsAsync()) as unknown as { granted: boolean };
  let finalGranted = existingStatus.granted;
  if (!finalGranted) {
    const requestStatus = (await Notifications.requestPermissionsAsync()) as unknown as { granted: boolean };
    finalGranted = requestStatus.granted;
  }
  if (!finalGranted) {
    return null;
  }

  const tokenData = await Notifications.getExpoPushTokenAsync({
    projectId: '3bb8f1f0-159c-4187-9aa3-be3afb099645',
  });
  return tokenData.data;
}

async function registerPushToken(token: string): Promise<void> {
  const deviceId = await getToken().catch(() => null);
  await fetchAPI('/api/v1/push/register', {}, {
    method: 'POST',
    body: JSON.stringify({
      expo_push_token: token,
      platform: Platform.OS,
      device_id: deviceId || '',
      alert_types: ['all'],
    }),
  });
}

export async function initPushNotifications(): Promise<string | null> {
  try {
    const token = await getExpoPushToken();
    if (token) {
      await registerPushToken(token);
    }
    return token;
  } catch (error) {
    console.error('[Push] Initialization failed:', error);
    return null;
  }
}

export function addPushNotificationListeners(
  onNotification?: (notification: Notifications.Notification) => void,
  onResponse?: (response: Notifications.NotificationResponse) => void,
): { subscription: Subscription | null; responseSubscription: Subscription | null } {
  const subscription = Notifications.addNotificationReceivedListener((notification) => {
    onNotification?.(notification);
  });

  const responseSubscription = Notifications.addNotificationResponseReceivedListener((response) => {
    onResponse?.(response);
  });

  return { subscription, responseSubscription };
}

export function removePushNotificationListeners(
  subscription: Subscription | null,
  responseSubscription: Subscription | null,
): void {
  if (subscription) {
    subscription.remove();
  }
  if (responseSubscription) {
    responseSubscription.remove();
  }
}
