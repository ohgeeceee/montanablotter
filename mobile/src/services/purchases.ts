import Purchases, { LOG_LEVEL } from 'react-native-purchases';
import { getToken } from './auth';
import { API_BASE_URL } from '../constants';

const REVENUECAT_API_KEY = process.env.EXPO_PUBLIC_REVENUECAT_API_KEY?.trim();
const REVENUECAT_APP_USER_ID = process.env.EXPO_PUBLIC_REVENUECAT_APP_USER_ID?.trim();

export const ENTITLEMENT_PREMIUM = 'premium';
export const OFFERING_ID = 'premium';

export async function initPurchases() {
  if (!REVENUECAT_API_KEY) {
    console.warn('[Purchases] RevenueCat API key not configured');
    return;
  }
  const configured = await Purchases.isConfigured();
  if (configured) {
    return;
  }
  Purchases.setLogLevel(LOG_LEVEL.INFO);
  Purchases.configure({
    apiKey: REVENUECAT_API_KEY,
    appUserID: REVENUECAT_APP_USER_ID || undefined,
  });
}

export async function getCustomerInfo() {
  return Purchases.getCustomerInfo();
}

export async function getOfferings() {
  const offerings = await Purchases.getOfferings();
  if (offerings.current) {
    return offerings;
  }
  // Fallback: if current offering is missing, return raw offerings for debugging
  return offerings;
}

export async function purchasePackage(packageToPurchase: any) {
  return Purchases.purchasePackage(packageToPurchase);
}

export async function restorePurchases() {
  return Purchases.restorePurchases();
}

export function checkPremiumEntitlement(customerInfo: any): boolean {
  return Boolean(customerInfo.entitlements.active[ENTITLEMENT_PREMIUM]);
}

export async function verifyPurchaseWithBackend(): Promise<boolean> {
  const token = await getToken();
  if (!token) {
    return false;
  }
  const appUserID = REVENUECAT_APP_USER_ID || (await Purchases.getCustomerInfo())?.originalAppUserId;
  if (!appUserID) {
    return false;
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/purchases/verify`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ app_user_id: appUserID }),
  });

  if (!response.ok) {
    console.warn('[Purchases] Backend verification failed:', response.status);
    return false;
  }

  const data = await response.json();
  return Boolean(data.is_premium);
}
