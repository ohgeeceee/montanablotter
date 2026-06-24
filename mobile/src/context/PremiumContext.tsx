import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { AppState, AppStateStatus } from 'react-native';
import { getCustomerInfo, checkPremiumEntitlement, verifyPurchaseWithBackend } from '../services/purchases';

interface PremiumContextValue {
  isPremium: boolean;
  loading: boolean;
  refresh: () => Promise<void>;
}

const PremiumContext = createContext<PremiumContextValue>({
  isPremium: false,
  loading: true,
  refresh: async () => {},
});

export function usePremium() {
  return useContext(PremiumContext);
}

export function PremiumProvider({ children }: { children: React.ReactNode }) {
  const [isPremium, setIsPremium] = useState(false);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const customerInfo = await getCustomerInfo();
      const localPremium = checkPremiumEntitlement(customerInfo);
      // Also verify server-side if the user is logged in; falls back to local
      // entitlement if backend verification is unavailable.
      const serverPremium = await verifyPurchaseWithBackend();
      setIsPremium(serverPremium || localPremium);
    } catch (err) {
      console.error('[PremiumContext] Refresh failed:', err);
      setIsPremium(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextAppState: AppStateStatus) => {
      if (nextAppState === 'active') {
        refresh();
      }
    });
    return () => subscription.remove();
  }, [refresh]);

  return (
    <PremiumContext.Provider value={{ isPremium, loading, refresh }}>
      {children}
    </PremiumContext.Provider>
  );
}
