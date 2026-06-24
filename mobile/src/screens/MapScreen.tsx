import React, { useState, useCallback } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import MapView, { Marker, Region } from 'react-native-maps';
import { usePremium } from '../context/PremiumContext';
import { mapApi, MapIncident } from '../services/api';
import { COLORS } from '../constants';

const DEFAULT_REGION: Region = {
  latitude: 46.8797,
  longitude: -110.3626,
  latitudeDelta: 8,
  longitudeDelta: 8,
};

export default function MapScreen() {
  const { isPremium } = usePremium();
  const navigation = useNavigation<any>();
  const [incidents, setIncidents] = useState<MapIncident[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchIncidents = useCallback(async (region: Region) => {
    setLoading(true);
    setError(null);
    try {
      const sw_lat = region.latitude - region.latitudeDelta / 2;
      const ne_lat = region.latitude + region.latitudeDelta / 2;
      const sw_lng = region.longitude - region.longitudeDelta / 2;
      const ne_lng = region.longitude + region.longitudeDelta / 2;
      const response = await mapApi.getIncidents({ sw_lat, sw_lng, ne_lat, ne_lng });
      setIncidents(response.incidents);
    } catch (err: any) {
      setError(err.message || 'Failed to load incidents.');
    } finally {
      setLoading(false);
    }
  }, []);

  const onRegionChangeComplete = useCallback(
    (region: Region) => {
      fetchIncidents(region);
    },
    [fetchIncidents]
  );

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
    <View style={styles.container}>
      <MapView
        style={styles.map}
        initialRegion={DEFAULT_REGION}
        onRegionChangeComplete={onRegionChangeComplete}
      >
        {incidents.map((incident) => (
          <Marker
            key={incident.id}
            coordinate={{
              latitude: incident.lat,
              longitude: incident.lng,
            }}
            title={incident.incident}
            description={`${incident.location} · ${incident.county} County · ${incident.date}`}
          />
        ))}
      </MapView>
      {loading && (
        <View style={styles.overlay}>
          <ActivityIndicator color={COLORS.primary} />
        </View>
      )}
      {error && (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}
      <View style={styles.countBadge}>
        <Text style={styles.countText}>{incidents.length} incidents visible</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  map: { flex: 1 },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    padding: 24,
  },
  icon: { fontSize: 48, marginBottom: 16 },
  lockTitle: { fontSize: 22, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  lockSubtext: {
    fontSize: 15,
    color: COLORS.secondary,
    textAlign: 'center',
    lineHeight: 22,
    marginBottom: 24,
  },
  upgradeButton: {
    backgroundColor: COLORS.accent,
    borderRadius: 10,
    paddingVertical: 14,
    paddingHorizontal: 24,
  },
  upgradeButtonText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.3)',
  },
  errorBanner: {
    position: 'absolute',
    top: 16,
    left: 16,
    right: 16,
    backgroundColor: COLORS.error,
    borderRadius: 8,
    padding: 12,
  },
  errorText: { color: '#fff', fontSize: 13, fontWeight: '600' },
  countBadge: {
    position: 'absolute',
    bottom: 24,
    left: 16,
    backgroundColor: COLORS.card,
    borderRadius: 8,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  countText: { fontSize: 12, fontWeight: '700', color: COLORS.primary },
});
