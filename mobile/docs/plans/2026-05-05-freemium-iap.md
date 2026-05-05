# Freemium IAP Implementation Plan — Montana Blotter Mobile

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a freemium tier with in-app purchases (IAP) to the Montana Blotter mobile app, offering premium features while keeping the core feed free.

**Architecture:** A client-side entitlement gate using `react-native-purchases` (RevenueCat SDK) for cross-platform IAP. RevenueCat handles store receipts, subscription lifecycle, and entitlements. The mobile app checks entitlements locally to gate premium UI (no backend auth needed for MVP). A Premium tab in the bottom navigator showcases features and handles purchase flow. Feature gates are simple boolean checks on the `premium` entitlement.

**Tech Stack:** Expo SDK 49, React Native 0.72, TypeScript, RevenueCat (`react-native-purchases`), React Navigation bottom-tabs, AsyncStorage for local premium feature state (alerts config, map bookmarks).

---

## Background: Current App State

- **Package:** `montanablotter` v1.0.0, Expo SDK 49, React Native 0.72.10
- **Navigation:** Bottom tabs (Feed, Jail Rosters, Laws, Blog) via `@react-navigation/bottom-tabs`
- **API:** Reads from `https://montanablotter.com/api/*` — public, no auth
- **State:** No global state management (useState/useEffect per screen)
- **Storage:** No persistent storage yet
- **No existing IAP, auth, or user accounts**

---

## Freemium Tier Design

### Free Tier (always available)
- Browse full blotter feed
- Search and county filters
- View post details
- Jail rosters list
- Montana laws reference
- Blog articles

### Premium Tier (gated)
| Feature | Free | Premium |
|---------|------|---------|
| Blotter feed | Unlimited | Unlimited |
| Search / filters | Basic | Advanced (date range, agency type) |
| Post detail | Full | Full + "Save to Watchlist" |
| Alerts | — | Custom alerts by county + incident type |
| Map view | — | Interactive crime map with heatmap |
| Export | — | Export incident data (CSV/JSON) |
| Ad-free | — | No ads (placeholder) |

### Products (RevenueCat)
- `mb_premium_monthly` — $4.99/mo
- `mb_premium_yearly` — $39.99/yr (33% savings)
- `mb_premium_lifetime` — $99.99 one-time

---

## Task List

### Task 1: Install RevenueCat SDK and configure entitlements

**Objective:** Add `react-native-purchases` dependency and initialize RevenueCat in the app entry point.

**Files:**
- Modify: `mobile/package.json`
- Modify: `mobile/App.tsx`
- Create: `mobile/src/services/purchases.ts`
- Modify: `mobile/.env.example`
- Modify: `mobile/.env`

**Step 1: Install dependency**

```bash
cd /root/montanablotter/mobile
npm install react-native-purchases@^7.0.0
```

**Step 2: Create purchases service**

Create `mobile/src/services/purchases.ts`:

```typescript
import Purchases, { LOG_LEVEL } from 'react-native-purchases';

const REVENUECAT_API_KEY = process.env.EXPO_PUBLIC_REVENUECAT_API_KEY?.trim();
const REVENUECAT_APP_USER_ID = process.env.EXPO_PUBLIC_REVENUECAT_APP_USER_ID?.trim();

export const ENTITLEMENT_PREMIUM = 'premium';

export async function initPurchases() {
  if (!REVENUECAT_API_KEY) {
    console.warn('[Purchases] RevenueCat API key not configured');
    return;
  }

  if (Purchases.isConfigured()) {
    return;
  }

  Purchases.setLogLevel(__DEV__ ? LOG_LEVEL.DEBUG : LOG_LEVEL.INFO);
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
```

**Step 3: Initialize in App.tsx**

Modify `mobile/App.tsx` — add `initPurchases()` call inside a `useEffect` in App component:

```typescript
import { initPurchases } from './src/services/purchases';

function App() {
  useEffect(() => {
    initPurchases().catch((err) => {
      console.error('[Purchases] Init failed:', err);
    });
  }, []);
  // ... rest unchanged
}
```

Add `useEffect` and `useState` imports if not already present (they are not in current App.tsx — add them).

**Step 4: Update env files**

Add to `mobile/.env.example`:
```
EXPO_PUBLIC_REVENUECAT_API_KEY=
EXPO_PUBLIC_REVENUECAT_APP_USER_ID=
```

Add to `mobile/.env`:
```
EXPO_PUBLIC_REVENUECAT_API_KEY=your_revenuecat_public_sdk_key
EXPO_PUBLIC_REVENUECAT_APP_USER_ID=
```

**Step 5: Commit**

```bash
git add -A
git commit -m "feat(iap): add RevenueCat SDK and purchase service"
```

---

### Task 2: Create premium context provider for entitlement state

**Objective:** Provide a reactive `isPremium` boolean across the app via React Context, polling RevenueCat on app foreground.

**Files:**
- Create: `mobile/src/context/PremiumContext.tsx`
- Modify: `mobile/App.tsx` (wrap navigator)

**Step 1: Write context**

Create `mobile/src/context/PremiumContext.tsx`:

```typescript
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
```

**Step 2: Wrap App.tsx**

Modify `mobile/App.tsx` — wrap `<NavigationContainer>` with `<PremiumProvider>`:

```typescript
import { PremiumProvider } from './src/context/PremiumContext';

function App() {
  useEffect(() => {
    initPurchases().catch((err) => {
      console.error('[Purchases] Init failed:', err);
    });
  }, []);

  return (
    <PremiumProvider>
      <NavigationContainer>
        {/* ... existing navigator ... */}
      </NavigationContainer>
    </PremiumProvider>
  );
}
```

**Step 3: Commit**

```bash
git add -A
git commit -m "feat(iap): add PremiumContext for reactive entitlement state"
```

---

### Task 3: Create Premium upsell screen with purchase flow

**Objective:** Build a screen that displays offerings, handles purchase, restore, and shows loading/error states.

**Files:**
- Create: `mobile/src/screens/PremiumScreen.tsx`
- Modify: `mobile/App.tsx` (add Premium tab)

**Step 1: Write PremiumScreen**

Create `mobile/src/screens/PremiumScreen.tsx`:

```typescript
import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  StyleSheet,
  Alert,
} from 'react-native';
import { getOfferings, purchasePackage, restorePurchases } from '../services/purchases';
import { usePremium } from '../context/PremiumContext';
import { COLORS } from '../constants';

export default function PremiumScreen() {
  const { isPremium, refresh } = usePremium();
  const [offerings, setOfferings] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [purchasing, setPurchasing] = useState(false);

  const loadOfferings = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getOfferings();
      setOfferings(result);
    } catch (err) {
      console.error('[PremiumScreen] Failed to load offerings:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOfferings();
  }, [loadOfferings]);

  const handlePurchase = async (pkg: any) => {
    try {
      setPurchasing(true);
      await purchasePackage(pkg);
      await refresh();
      Alert.alert('Welcome to Premium!', 'Your purchase was successful.');
    } catch (err: any) {
      if (!err.userCancelled) {
        Alert.alert('Purchase failed', err.message || 'Something went wrong.');
      }
    } finally {
      setPurchasing(false);
    }
  };

  const handleRestore = async () => {
    try {
      setPurchasing(true);
      await restorePurchases();
      await refresh();
      Alert.alert('Restored', 'Your purchases have been restored.');
    } catch (err: any) {
      Alert.alert('Restore failed', err.message || 'Unable to restore purchases.');
    } finally {
      setPurchasing(false);
    }
  };

  if (isPremium) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.premiumBadge}>⭐ Premium Active</Text>
        <Text style={styles.premiumSubtext}>You have access to all premium features.</Text>
        <TouchableOpacity style={styles.restoreButton} onPress={handleRestore}>
          <Text style={styles.restoreButtonText}>Restore Purchases</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const currentOffering = offerings?.current;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Unlock Premium</Text>
      <Text style={styles.subtitle}>Support transparency journalism and get powerful tools.</Text>

      <View style={styles.featureList}>
        <Text style={styles.featureItem}>🔔 Custom alerts by county & incident type</Text>
        <Text style={styles.featureItem}>🗺️ Interactive crime map with heat layers</Text>
        <Text style={styles.featureItem}>📤 Export incident data (CSV / JSON)</Text>
        <Text style={styles.featureItem}>🔖 Save posts to personal watchlist</Text>
        <Text style={styles.featureItem}>🚫 Ad-free experience</Text>
      </View>

      {loading ? (
        <ActivityIndicator size="large" color={COLORS.primary} style={styles.loader} />
      ) : currentOffering ? (
        <View style={styles.packages}>
          {currentOffering.availablePackages.map((pkg: any) => (
            <TouchableOpacity
              key={pkg.identifier}
              style={styles.packageCard}
              onPress={() => handlePurchase(pkg)}
              disabled={purchasing}
            >
              <Text style={styles.packageTitle}>{pkg.product.title}</Text>
              <Text style={styles.packagePrice}>{pkg.product.priceString}</Text>
              <Text style={styles.packageDesc}>{pkg.product.description}</Text>
            </TouchableOpacity>
          ))}
        </View>
      ) : (
        <Text style={styles.errorText}>Unable to load subscription options.</Text>
      )}

      <TouchableOpacity style={styles.restoreButton} onPress={handleRestore} disabled={purchasing}>
        <Text style={styles.restoreButtonText}>Restore Purchases</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  content: { padding: 20, paddingBottom: 40 },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    padding: 20,
  },
  title: { fontSize: 28, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  subtitle: { fontSize: 15, color: COLORS.secondary, marginBottom: 24, lineHeight: 22 },
  featureList: { marginBottom: 24, gap: 10 },
  featureItem: { fontSize: 15, color: COLORS.primary, lineHeight: 22 },
  packages: { gap: 12, marginBottom: 20 },
  packageCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: COLORS.border,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 2,
  },
  packageTitle: { fontSize: 16, fontWeight: '700', color: COLORS.primary, marginBottom: 4 },
  packagePrice: { fontSize: 20, fontWeight: '800', color: COLORS.accent, marginBottom: 4 },
  packageDesc: { fontSize: 13, color: COLORS.secondary },
  restoreButton: {
    paddingVertical: 12,
    alignItems: 'center',
  },
  restoreButtonText: { fontSize: 14, color: COLORS.secondary, fontWeight: '600' },
  loader: { marginVertical: 24 },
  errorText: { fontSize: 14, color: COLORS.error, textAlign: 'center', marginVertical: 24 },
  premiumBadge: { fontSize: 22, fontWeight: '800', color: COLORS.accent, marginBottom: 8 },
  premiumSubtext: { fontSize: 15, color: COLORS.secondary, textAlign: 'center' },
});
```

**Step 2: Add Premium tab to App.tsx**

Modify `mobile/App.tsx`:
- Import `PremiumScreen`
- Add `"Premium"` tab with `diamond-outline` / `diamond` icon

Add import:
```typescript
import PremiumScreen from './src/screens/PremiumScreen';
```

Add tab:
```typescript
<Tab.Screen name="Premium" component={PremiumScreen} />
```

Add icon logic in `tabBarIcon`:
```typescript
} else if (route.name === 'Premium') {
  iconName = focused ? 'diamond' : 'diamond-outline';
}
```

**Step 3: Commit**

```bash
git add -A
git commit -m "feat(iap): add Premium upsell screen with purchase flow"
```

---

### Task 4: Gate advanced search filters behind premium

**Objective:** Add date-range and agency-type filters to FeedScreen, but disable them for free users with an upgrade prompt.

**Files:**
- Modify: `mobile/src/screens/FeedScreen.tsx`
- Modify: `mobile/src/services/api.ts` (add params)
- Modify: `mobile/src/types/index.ts` (add filter types)

**Step 1: Update types**

Add to `mobile/src/types/index.ts`:

```typescript
export interface FeedFilters {
  county?: string;
  agency_type?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
}
```

**Step 2: Update API service**

Modify `mobile/src/services/api.ts` — `getPosts` already accepts these params, no change needed. Ensure the signature is:

```typescript
getPosts: async (params: {
  page?: number;
  per_page?: number;
  county?: string;
  agency_type?: string;
  date_from?: string;
  date_to?: string;
  search?: string;
} = {}): Promise<PostsResponse>
```

It already is — no change.

**Step 3: Add premium-gated filters UI to FeedScreen**

Modify `mobile/src/screens/FeedScreen.tsx`:

Add imports:
```typescript
import { usePremium } from '../context/PremiumContext';
```

Add state inside component:
```typescript
const { isPremium } = usePremium();
const [showFilters, setShowFilters] = useState(false);
const [agencyType, setAgencyType] = useState('');
const [dateFrom, setDateFrom] = useState('');
const [dateTo, setDateTo] = useState('');
```

Update `fetchPosts` to pass new params:
```typescript
const fetchPosts = useCallback(async (
  pageNum: number = 1,
  searchQuery: string = '',
  county: string = '',
  filters: { agency_type?: string; date_from?: string; date_to?: string } = {}
) => {
  // ... existing try block ...
  const data = await api.getPosts({
    page: pageNum,
    per_page: 20,
    search: searchQuery,
    county,
    agency_type: filters.agency_type,
    date_from: filters.date_from,
    date_to: filters.date_to,
  });
  // ... rest unchanged
}, []);
```

Update effects that call `fetchPosts` to pass the new filter state. Add a collapsible filter panel below the county chips that shows agency type picker and date inputs, with a "Premium only" overlay if `!isPremium`.

Add a small touchable row: "Advanced filters ▼" that toggles `showFilters`.

When `showFilters` is true and `!isPremium`, render a blur overlay with text: "Advanced filters are a Premium feature" and a button "Upgrade to Premium" that navigates to the Premium tab.

For navigation to Premium tab from FeedScreen, use:
```typescript
const tabNavigation = useNavigation<any>();
// ...
tabNavigation.navigate('Premium');
```

**Step 4: Commit**

```bash
git add -A
git commit -m "feat(iap): gate advanced search filters behind premium entitlement"
```

---

### Task 5: Add "Save to Watchlist" premium feature on post detail

**Objective:** Allow premium users to save posts to a local watchlist using AsyncStorage.

**Files:**
- Install: `@react-native-async-storage/async-storage`
- Create: `mobile/src/services/watchlist.ts`
- Modify: `mobile/src/screens/PostDetailScreen.tsx`
- Modify: `mobile/src/types/index.ts`

**Step 1: Install AsyncStorage**

```bash
cd /root/montanablotter/mobile
npm install @react-native-async-storage/async-storage
```

**Step 2: Create watchlist service**

Create `mobile/src/services/watchlist.ts`:

```typescript
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Post } from '../types';

const WATCHLIST_KEY = '@mb_watchlist';

export async function getWatchlist(): Promise<Post[]> {
  const raw = await AsyncStorage.getItem(WATCHLIST_KEY);
  return raw ? JSON.parse(raw) : [];
}

export async function addToWatchlist(post: Post) {
  const list = await getWatchlist();
  if (list.find((p) => p.id === post.id)) {
    return;
  }
  const next = [post, ...list];
  await AsyncStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
}

export async function removeFromWatchlist(postId: number) {
  const list = await getWatchlist();
  const next = list.filter((p) => p.id !== postId);
  await AsyncStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
}

export async function isInWatchlist(postId: number): Promise<boolean> {
  const list = await getWatchlist();
  return list.some((p) => p.id === postId);
}
```

**Step 3: Add watchlist UI to PostDetailScreen**

Modify `mobile/src/screens/PostDetailScreen.tsx`:

Add imports:
```typescript
import { usePremium } from '../context/PremiumContext';
import { addToWatchlist, removeFromWatchlist, isInWatchlist } from '../services/watchlist';
```

Inside component, add state:
```typescript
const { isPremium } = usePremium();
const [saved, setSaved] = useState(false);
```

In `useEffect` that loads post, also check watchlist:
```typescript
const inList = await isInWatchlist(postId);
setSaved(inList);
```

Add a save button in the header area (next to the badge/date row). If `!isPremium`, show a disabled star with "Premium" label that, when pressed, shows an Alert prompting upgrade. If `isPremium`, toggle save state and call `addToWatchlist` / `removeFromWatchlist`.

**Step 4: Commit**

```bash
git add -A
git commit -m "feat(iap): add save-to-watchlist premium feature with AsyncStorage"
```

---

### Task 6: Add Watchlist screen (premium-gated tab or stack screen)

**Objective:** Create a screen to view saved watchlist posts, accessible from the Feed stack.

**Files:**
- Create: `mobile/src/screens/WatchlistScreen.tsx`
- Modify: `mobile/App.tsx` (add to FeedStack)
- Modify: `mobile/src/screens/FeedScreen.tsx` (add header button to navigate)

**Step 1: Create WatchlistScreen**

Create `mobile/src/screens/WatchlistScreen.tsx`:

```typescript
import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  RefreshControl,
} from 'react-native';
import { useNavigation, useFocusEffect } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { getWatchlist, removeFromWatchlist } from '../services/watchlist';
import { Post } from '../types';
import { COLORS } from '../constants';

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
  Watchlist: undefined;
};

export default function WatchlistScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<FeedStackParamList>>();
  const [posts, setPosts] = useState<Post[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const list = await getWatchlist();
    setPosts(list);
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const handleRemove = async (postId: number) => {
    await removeFromWatchlist(postId);
    await load();
  };

  const renderItem = ({ item }: { item: Post }) => (
    <TouchableOpacity
      style={styles.card}
      onPress={() => navigation.navigate('PostDetail', { postId: item.id })}
    >
      <View style={styles.row}>
        <Text style={styles.title} numberOfLines={2}>{item.title}</Text>
        <TouchableOpacity onPress={() => handleRemove(item.id)}>
          <Text style={styles.remove}>✕</Text>
        </TouchableOpacity>
      </View>
      <Text style={styles.meta}>{item.county} County · {item.agency_name}</Text>
    </TouchableOpacity>
  );

  return (
    <View style={styles.container}>
      <FlatList
        data={posts}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderItem}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No saved posts yet.</Text>
            <Text style={styles.emptySubtext}>Premium users can save posts to their watchlist.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  list: { padding: 16 },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  title: { flex: 1, fontSize: 15, fontWeight: '700', color: COLORS.primary, marginRight: 8 },
  remove: { fontSize: 16, color: COLORS.error, fontWeight: '700' },
  meta: { fontSize: 12, color: COLORS.secondary, marginTop: 6 },
  empty: { padding: 40, alignItems: 'center' },
  emptyText: { fontSize: 16, fontWeight: '700', color: COLORS.primary, marginBottom: 4 },
  emptySubtext: { fontSize: 14, color: COLORS.secondary, textAlign: 'center' },
});
```

**Step 2: Add to FeedStack in App.tsx**

```typescript
<FeedStack.Screen name="Watchlist" component={WatchlistScreen} options={{ title: 'Watchlist' }} />
```

Add import for `WatchlistScreen`.

Update `FeedStackParamList` type:
```typescript
type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
  Watchlist: undefined;
};
```

**Step 3: Add navigation button in FeedScreen header**

Modify `mobile/src/screens/FeedScreen.tsx` — in the hero section, add a small bookmark icon row that navigates to Watchlist. If `!isPremium`, show it disabled with an upgrade prompt.

**Step 4: Commit**

```bash
git add -A
git commit -m "feat(iap): add Watchlist screen for saved posts"
```

---

### Task 7: Add premium-gated Alerts configuration screen

**Objective:** Allow premium users to configure push-like local alerts for new incidents matching county + incident type. Use local state + AsyncStorage; actual push notifications are out of scope for MVP.

**Files:**
- Create: `mobile/src/screens/AlertsScreen.tsx`
- Create: `mobile/src/services/alerts.ts`
- Modify: `mobile/App.tsx` (add to FeedStack)

**Step 1: Create alerts service**

Create `mobile/src/services/alerts.ts`:

```typescript
import AsyncStorage from '@react-native-async-storage/async-storage';

export interface AlertRule {
  id: string;
  county: string;
  incident_type: string;
  enabled: boolean;
}

const ALERTS_KEY = '@mb_alert_rules';

export async function getAlertRules(): Promise<AlertRule[]> {
  const raw = await AsyncStorage.getItem(ALERTS_KEY);
  return raw ? JSON.parse(raw) : [];
}

export async function saveAlertRules(rules: AlertRule[]) {
  await AsyncStorage.setItem(ALERTS_KEY, JSON.stringify(rules));
}

export async function addAlertRule(rule: AlertRule) {
  const rules = await getAlertRules();
  rules.push(rule);
  await saveAlertRules(rules);
}

export async function removeAlertRule(id: string) {
  const rules = await getAlertRules();
  await saveAlertRules(rules.filter((r) => r.id !== id));
}

export async function toggleAlertRule(id: string, enabled: boolean) {
  const rules = await getAlertRules();
  const next = rules.map((r) => (r.id === id ? { ...r, enabled } : r));
  await saveAlertRules(next);
}
```

**Step 2: Create AlertsScreen**

Create `mobile/src/screens/AlertsScreen.tsx`:

```typescript
import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  StyleSheet,
  Switch,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { usePremium } from '../context/PremiumContext';
import { getAlertRules, addAlertRule, removeAlertRule, toggleAlertRule, AlertRule } from '../services/alerts';
import { COLORS } from '../constants';

export default function AlertsScreen() {
  const { isPremium } = usePremium();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [county, setCounty] = useState('');
  const [incidentType, setIncidentType] = useState('');

  const load = useCallback(async () => {
    const data = await getAlertRules();
    setRules(data);
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handleAdd = async () => {
    if (!county.trim() || !incidentType.trim()) return;
    const rule: AlertRule = {
      id: `${Date.now()}`,
      county: county.trim(),
      incident_type: incidentType.trim(),
      enabled: true,
    };
    await addAlertRule(rule);
    setCounty('');
    setIncidentType('');
    await load();
  };

  const handleRemove = async (id: string) => {
    await removeAlertRule(id);
    await load();
  };

  const handleToggle = async (id: string, value: boolean) => {
    await toggleAlertRule(id, value);
    await load();
  };

  if (!isPremium) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.lockTitle}>🔒 Alerts are Premium</Text>
        <Text style={styles.lockSubtext}>Upgrade to create custom incident alerts.</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.form}>
        <Text style={styles.label}>County</Text>
        <TextInput style={styles.input} value={county} onChangeText={setCounty} placeholder="e.g. Gallatin" />
        <Text style={styles.label}>Incident Type</Text>
        <TextInput style={styles.input} value={incidentType} onChangeText={setIncidentType} placeholder="e.g. DUI" />
        <TouchableOpacity style={styles.addButton} onPress={handleAdd}>
          <Text style={styles.addButtonText}>Add Alert Rule</Text>
        </TouchableOpacity>
      </View>
      <FlatList
        data={rules}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <View style={styles.ruleCard}>
            <View style={styles.ruleRow}>
              <View>
                <Text style={styles.ruleCounty}>{item.county} County</Text>
                <Text style={styles.ruleType}>{item.incident_type}</Text>
              </View>
              <Switch value={item.enabled} onValueChange={(v) => handleToggle(item.id, v)} />
            </View>
            <TouchableOpacity onPress={() => handleRemove(item.id)}>
              <Text style={styles.removeText}>Remove</Text>
            </TouchableOpacity>
          </View>
        )}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No alert rules yet.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    padding: 20,
  },
  lockTitle: { fontSize: 20, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  lockSubtext: { fontSize: 15, color: COLORS.secondary, textAlign: 'center' },
  form: { padding: 16, backgroundColor: COLORS.card, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  label: { fontSize: 13, fontWeight: '700', color: COLORS.secondary, textTransform: 'uppercase', marginBottom: 6, marginTop: 12 },
  input: {
    backgroundColor: COLORS.background,
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    color: COLORS.primary,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  addButton: {
    backgroundColor: COLORS.primary,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: 16,
  },
  addButtonText: { color: COLORS.card, fontSize: 15, fontWeight: '700' },
  list: { padding: 16 },
  ruleCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  ruleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  ruleCounty: { fontSize: 15, fontWeight: '700', color: COLORS.primary },
  ruleType: { fontSize: 13, color: COLORS.secondary, marginTop: 2 },
  removeText: { fontSize: 13, color: COLORS.error, fontWeight: '600', marginTop: 8 },
  empty: { padding: 40, alignItems: 'center' },
  emptyText: { fontSize: 15, color: COLORS.secondary },
});
```

**Step 3: Add to FeedStack in App.tsx**

```typescript
<FeedStack.Screen name="Alerts" component={AlertsScreen} options={{ title: 'Alerts' }} />
```

Add import. Update `FeedStackParamList`:
```typescript
type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
  Watchlist: undefined;
  Alerts: undefined;
};
```

**Step 4: Commit**

```bash
git add -A
git commit -m "feat(iap): add premium-gated Alerts configuration screen"
```

---

### Task 8: Add premium-gated Crime Map placeholder screen

**Objective:** Add a Map tab/screen that shows a placeholder for the interactive crime map, gated behind premium. Full map implementation (geocoding, heatmap, etc.) is a large follow-up project.

**Files:**
- Create: `mobile/src/screens/MapScreen.tsx`
- Modify: `mobile/App.tsx` (add Map tab)

**Step 1: Create MapScreen**

Create `mobile/src/screens/MapScreen.tsx`:

```typescript
import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { usePremium } from '../context/PremiumContext';
import { COLORS } from '../constants';

export default function MapScreen() {
  const { isPremium } = usePremium();
  const navigation = useNavigation<any>();

  if (!isPremium) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.icon}>🗺️</Text>
        <Text style={styles.lockTitle}>Crime Map is Premium</Text>
        <Text style={styles.lockSubtext}>
          Explore incident distributions with heat maps and historical layers.
        </Text>
        <TouchableOpacity style={styles.upgradeButton} onPress={() => navigation.navigate('Premium')}>
          <Text style={styles.upgradeButtonText}>Upgrade to Premium</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={styles.centerContainer}>
      <Text style={styles.icon}>🗺️</Text>
      <Text style={styles.title}>Crime Map</Text>
      <Text style={styles.subtext}>
        Interactive map with heat layers and historical comparisons coming soon.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    padding: 24,
  },
  icon: { fontSize: 48, marginBottom: 16 },
  title: { fontSize: 22, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  subtext: { fontSize: 15, color: COLORS.secondary, textAlign: 'center', lineHeight: 22 },
  lockTitle: { fontSize: 22, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  lockSubtext: { fontSize: 15, color: COLORS.secondary, textAlign: 'center', lineHeight: 22, marginBottom: 24 },
  upgradeButton: {
    backgroundColor: COLORS.accent,
    borderRadius: 10,
    paddingVertical: 14,
    paddingHorizontal: 24,
  },
  upgradeButtonText: { color: '#fff', fontSize: 15, fontWeight: '700' },
});
```

**Step 2: Add Map tab to App.tsx**

Add import, add tab:
```typescript
<Tab.Screen name="Map" component={MapScreen} />
```

Add icon logic:
```typescript
} else if (route.name === 'Map') {
  iconName = focused ? 'map' : 'map-outline';
}
```

**Step 3: Commit**

```bash
git add -A
git commit -m "feat(iap): add premium-gated Crime Map placeholder screen"
```

---

### Task 9: Add premium-gated Export feature on post detail

**Objective:** Allow premium users to export a single post's data as JSON or shareable text.

**Files:**
- Modify: `mobile/src/screens/PostDetailScreen.tsx`
- Install: `expo-sharing` (if not already present)

**Step 1: Install expo-sharing**

```bash
cd /root/montanablotter/mobile
npx expo install expo-sharing
```

**Step 2: Add export UI to PostDetailScreen**

Modify `mobile/src/screens/PostDetailScreen.tsx`:

Add imports:
```typescript
import * as Sharing from 'expo-sharing';
import { usePremium } from '../context/PremiumContext';
```

Inside component:
```typescript
const { isPremium } = usePremium();
```

Add an export section below the PDF button:

```typescript
{isPremium && post && (
  <View style={styles.exportSection}>
    <Text style={styles.summaryTitle}>Export</Text>
    <TouchableOpacity
      style={styles.exportButton}
      onPress={async () => {
        const payload = JSON.stringify(post, null, 2);
        // Write to temp file and share
        // Simplified: use Share API from react-native
      }}
    >
      <Text style={styles.exportButtonText}>Share as JSON</Text>
    </TouchableOpacity>
  </View>
)}
```

For simplicity, use React Native's `Share` API (no extra dep):

```typescript
import { Share } from 'react-native';
```

And the onPress:
```typescript
onPress={async () => {
  await Share.share({
    message: JSON.stringify(post, null, 2),
    title: post.title,
  });
}}
```

Add styles for `exportSection`, `exportButton`, `exportButtonText`.

If `!isPremium`, show a small "Export available with Premium" text row.

**Step 3: Commit**

```bash
git add -A
git commit -m "feat(iap): add premium-gated export feature on post detail"
```

---

### Task 10: Wire up RevenueCat offering identifiers and test purchases

**Objective:** Ensure the purchase flow uses the correct RevenueCat offering and package identifiers. Add a debug helper for testing.

**Files:**
- Modify: `mobile/src/services/purchases.ts`
- Modify: `mobile/src/screens/PremiumScreen.tsx`

**Step 1: Add offering identifier constant**

Modify `mobile/src/services/purchases.ts`:

```typescript
export const OFFERING_ID = 'premium';
```

Update `getOfferings` to fallback:
```typescript
export async function getOfferings() {
  const offerings = await Purchases.getOfferings();
  if (offerings.current) {
    return offerings;
  }
  // Fallback: try to fetch the specific offering by ID
  return offerings;
}
```

**Step 2: Add debug info in PremiumScreen (dev only)**

In `PremiumScreen`, below the restore button, if `__DEV__`, show a small text block with `JSON.stringify(offerings, null, 2)` for debugging.

**Step 3: Commit**

```bash
git add -A
git commit -m "chore(iap): wire RevenueCat offering IDs and add debug helpers"
```

---

### Task 11: Final integration review and typecheck

**Objective:** Run TypeScript typecheck, verify all imports resolve, and ensure no runtime errors from missing dependencies.

**Files:**
- All modified files

**Step 1: Typecheck**

```bash
cd /root/montanablotter/mobile
npm run typecheck
```

**Step 2: Fix any type errors**

Address missing types, incorrect imports, or navigation param list mismatches.

**Step 3: Verify package.json integrity**

```bash
npm ci
```

**Step 4: Commit fixes**

```bash
git add -A
git commit -m "fix(iap): resolve type errors and integration issues"
```

---

## Post-Implementation Notes

### RevenueCat Dashboard Setup (manual, not in code)
1. Create products in App Store Connect / Google Play Console:
   - `mb_premium_monthly` — consumable/auto-renewable subscription
   - `mb_premium_yearly` — auto-renewable subscription
   - `mb_premium_lifetime` — non-consumable
2. In RevenueCat dashboard, create an Offering named `premium`
3. Add packages with identifiers matching the store products
4. Copy the public SDK API key into `.env` as `EXPO_PUBLIC_REVENUECAT_API_KEY`

### Testing IAP
- Use StoreKit Configuration file for iOS simulator testing
- Use Google Play test tracks for Android
- RevenueCat sandbox purchases are free and auto-renew on accelerated schedule

### Future Enhancements
- Backend receipt validation + user accounts
- Server-side push notifications for alerts
- Full interactive map with Mapbox/Leaflet
- CSV export with multiple posts
- Family sharing support
