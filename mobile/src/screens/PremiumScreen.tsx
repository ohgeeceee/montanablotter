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

      {__DEV__ && offerings && (
        <View style={styles.debugBox}>
          <Text style={styles.debugText}>{JSON.stringify(offerings, null, 2)}</Text>
        </View>
      )}
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
  debugBox: {
    marginTop: 20,
    padding: 12,
    backgroundColor: '#f1f5f9',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  debugText: { fontSize: 10, color: COLORS.secondary, fontFamily: 'monospace' },
});
