import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TextInput,
  StyleSheet,
  RefreshControl,
  ActivityIndicator,
  TouchableOpacity,
  Linking,
} from 'react-native';
import { Warrant } from '../types';
import { warrantApi } from '../services/api';
import { COLORS } from '../constants';

const TYPE_FILTERS: { key: string; label: string }[] = [
  { key: '', label: 'All' },
  { key: 'arrest', label: 'Arrest' },
  { key: 'bench', label: 'Bench' },
  { key: 'felony', label: 'Felony' },
  { key: 'misdemeanor', label: 'Misdemeanor' },
];

export default function WarrantsScreen() {
  const [warrants, setWarrants] = useState<Warrant[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState('');
  const [warrantType, setWarrantType] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadWarrants = useCallback(async (isRefresh = false) => {
    if (!isRefresh) setLoading(true);
    setError(null);
    try {
      const response = await warrantApi.getWarrants({
        q: q.trim(),
        warrant_type: warrantType,
        limit: 50,
      });
      setWarrants(response.warrants);
      setTotal(response.total);
    } catch (err) {
      setError('Could not load warrants. Pull down to retry.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [q, warrantType]);

  useEffect(() => {
    loadWarrants();
  }, [loadWarrants]);

  const onRefresh = () => {
    setRefreshing(true);
    loadWarrants(true);
  };

  const openUrl = (url: string) => {
    if (!url) return;
    Linking.openURL(url).catch((err) => console.error('Failed to open URL:', err));
  };

  const renderWarrant = ({ item }: { item: Warrant }) => (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.name}>{item.person_name || 'Unknown'}</Text>
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{item.warrant_type || 'Warrant'}</Text>
        </View>
      </View>
      <View style={styles.metaRow}>
        {item.county ? <Text style={styles.meta}>{item.county} County</Text> : null}
        {item.city ? <Text style={styles.meta}>• {item.city}</Text> : null}
        {item.issue_date ? <Text style={styles.meta}>• {item.issue_date}</Text> : null}
      </View>
      {item.charges_text ? (
        <Text style={styles.charges} numberOfLines={4}>
          {item.charges_text}
        </Text>
      ) : null}
      <View style={styles.footer}>
        {item.bond_amount ? (
          <Text style={styles.bond}>Bond: {item.bond_amount}</Text>
        ) : (
          <View />
        )}
        {item.source_url ? (
          <TouchableOpacity onPress={() => openUrl(item.source_url)}>
            <Text style={styles.sourceLink}>Source →</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerEyebrow}>Active Warrants</Text>
        <Text style={styles.headerTitle}>Montana Warrants</Text>
        <Text style={styles.headerSubtitle}>
          Active warrant records from participating counties
        </Text>
      </View>
      <View style={styles.controls}>
        <View style={styles.filterRow}>
          {TYPE_FILTERS.map((filter) => (
            <TouchableOpacity
              key={filter.key}
              style={[styles.filterChip, warrantType === filter.key && styles.filterChipActive]}
              onPress={() => setWarrantType(filter.key)}
            >
              <Text style={[styles.filterChipText, warrantType === filter.key && styles.filterChipTextActive]}>
                {filter.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
        <TextInput
          style={styles.searchInput}
          placeholder="Search name, charges, county..."
          value={q}
          onChangeText={setQ}
          placeholderTextColor={COLORS.secondary}
          returnKeyType="search"
        />
        <Text style={styles.resultsText}>
          {loading ? 'Loading...' : `${warrants.length} shown • ${total} total active`}
        </Text>
      </View>
      {loading && !refreshing ? (
        <View style={styles.loader}>
          <ActivityIndicator size="large" color={COLORS.accent} />
        </View>
      ) : (
        <FlatList
          data={warrants}
          keyExtractor={(item) => `warrant-${item.id}`}
          renderItem={renderWarrant}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>{error || 'No warrants matched your filters.'}</Text>
            </View>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    backgroundColor: COLORS.primary,
    padding: 20,
    paddingTop: 24,
  },
  headerEyebrow: {
    fontSize: 12,
    fontWeight: '700',
    color: '#fdba74',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: '700',
    color: COLORS.card,
    marginBottom: 8,
  },
  headerSubtitle: {
    fontSize: 14,
    color: '#94a3b8',
    lineHeight: 20,
  },
  controls: {
    padding: 16,
    backgroundColor: COLORS.card,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  filterRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 12,
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: COLORS.background,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  filterChipActive: {
    backgroundColor: COLORS.primary,
    borderColor: COLORS.primary,
  },
  filterChipText: {
    fontSize: 12,
    fontWeight: '600',
    color: COLORS.secondary,
  },
  filterChipTextActive: {
    color: COLORS.card,
  },
  searchInput: {
    backgroundColor: COLORS.background,
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    color: COLORS.primary,
  },
  resultsText: {
    marginTop: 10,
    fontSize: 12,
    color: COLORS.secondary,
  },
  loader: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  list: {
    padding: 16,
  },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 8,
  },
  name: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    flex: 1,
    marginRight: 8,
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: '#ffedd5',
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
    color: '#c2410c',
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  meta: {
    fontSize: 12,
    color: COLORS.secondary,
    fontWeight: '600',
  },
  charges: {
    fontSize: 13,
    color: COLORS.primary,
    lineHeight: 18,
    marginBottom: 10,
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    paddingTop: 10,
  },
  bond: {
    fontSize: 12,
    color: COLORS.secondary,
  },
  sourceLink: {
    fontSize: 12,
    fontWeight: '700',
    color: COLORS.accent,
  },
  emptyContainer: {
    paddingVertical: 40,
    alignItems: 'center',
  },
  emptyText: {
    color: COLORS.secondary,
    fontSize: 14,
    textAlign: 'center',
  },
});
