import Purchases, { LOG_LEVEL } from 'react-native-purchases';

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
  return Purchases.getOfferings();
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
