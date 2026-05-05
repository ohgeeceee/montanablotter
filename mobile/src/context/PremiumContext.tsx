import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { AppState, AppStateStatus } from 'react-native';
import { getCustomerInfo, checkPremiumEntitlement } from '../services/purchases';

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
      setIsPremium(checkPremiumEntitlement(customerInfo));
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
