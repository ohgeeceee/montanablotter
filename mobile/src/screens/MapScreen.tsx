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
