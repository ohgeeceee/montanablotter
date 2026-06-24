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
  Image,
} from 'react-native';
import { MissingPerson } from '../types';
import { missingPersonsApi } from '../services/api';
import { COLORS } from '../constants';

const STATUS_FILTERS: { key: 'missing' | 'located' | ''; label: string }[] = [
  { key: 'missing', label: 'Active' },
  { key: 'located', label: 'Located' },
  { key: '', label: 'All' },
];

export default function PeopleScreen() {
  const [people, setPeople] = useState<MissingPerson[]>([]);
  const [totalActive, setTotalActive] = useState(0);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<'missing' | 'located' | ''>('missing');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadPeople = useCallback(async (isRefresh = false) => {
    if (!isRefresh) setLoading(true);
    setError(null);
    try {
      const response = await missingPersonsApi.getPeople({
        status,
        q: q.trim(),
        sort: 'updated_desc',
        limit: 50,
      });
      setPeople(response.people);
      setTotalActive(response.total_active);
    } catch (err) {
      setError('Could not load missing persons. Pull down to retry.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [q, status]);

  useEffect(() => {
    loadPeople();
  }, [loadPeople]);

  const onRefresh = () => {
    setRefreshing(true);
    loadPeople(true);
  };

  const openUrl = (url: string) => {
    if (!url) return;
    Linking.openURL(url).catch((err) => console.error('Failed to open URL:', err));
  };

  const renderPerson = ({ item }: { item: MissingPerson }) => (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={styles.photoColumn}>
          {item.photo_url || item.photos[0]?.url ? (
            <Image
              source={{ uri: item.photo_url || item.photos[0].url }}
              style={styles.photo}
              resizeMode="cover"
            />
          ) : (
            <View style={styles.photoPlaceholder}>
              <Text style={styles.photoPlaceholderText}>?</Text>
            </View>
          )}
        </View>
        <View style={styles.infoColumn}>
          <View style={styles.nameRow}>
            <Text style={styles.name}>{item.full_name || 'Unknown'}</Text>
            {item.is_active ? (
              <View style={styles.activeBadge}>
                <Text style={styles.activeBadgeText}>Active</Text>
              </View>
            ) : (
              <View style={styles.locatedBadge}>
                <Text style={styles.locatedBadgeText}>Located</Text>
              </View>
            )}
          </View>
          <View style={styles.metaList}>
            {item.age ? <Text style={styles.meta}>Age {item.age}</Text> : null}
            {item.last_seen_location ? (
              <Text style={styles.meta} numberOfLines={1}>
                Last seen: {item.last_seen_location}
              </Text>
            ) : null}
            {item.date_last_seen ? <Text style={styles.meta}>Date: {item.date_last_seen.slice(0, 10)}</Text> : null}
            {item.investigating_agency ? (
              <Text style={styles.meta} numberOfLines={1}>
                Agency: {item.investigating_agency}
              </Text>
            ) : null}
          </View>
        </View>
      </View>
      {item.summary ? (
        <Text style={styles.summary} numberOfLines={3}>
          {item.summary}
        </Text>
      ) : null}
      <View style={styles.footer}>
        <Text style={styles.caseNumber}>{item.case_number ? `Case #${item.case_number}` : ''}</Text>
        {item.public_href ? (
          <TouchableOpacity onPress={() => openUrl(`https://montanablotter.com${item.public_href}`)}>
            <Text style={styles.sourceLink}>Details →</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerEyebrow}>People Directory</Text>
        <Text style={styles.headerTitle}>Missing Persons</Text>
        <Text style={styles.headerSubtitle}>
          Active alerts and located persons from Montana DOJ
        </Text>
      </View>
      <View style={styles.controls}>
        <View style={styles.filterRow}>
          {STATUS_FILTERS.map((filter) => (
            <TouchableOpacity
              key={filter.key}
              style={[styles.filterChip, status === filter.key && styles.filterChipActive]}
              onPress={() => setStatus(filter.key)}
            >
              <Text style={[styles.filterChipText, status === filter.key && styles.filterChipTextActive]}>
                {filter.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
        <TextInput
          style={styles.searchInput}
          placeholder="Search name, location, case number..."
          value={q}
          onChangeText={setQ}
          placeholderTextColor={COLORS.secondary}
          returnKeyType="search"
        />
        <Text style={styles.resultsText}>
          {loading ? 'Loading...' : `${people.length} shown • ${totalActive} active statewide`}
        </Text>
      </View>
      {loading && !refreshing ? (
        <View style={styles.loader}>
          <ActivityIndicator size="large" color={COLORS.accent} />
        </View>
      ) : (
        <FlatList
          data={people}
          keyExtractor={(item) => `person-${item.id}`}
          renderItem={renderPerson}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>{error || 'No records matched your filters.'}</Text>
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
    marginBottom: 12,
  },
  photoColumn: {
    marginRight: 14,
  },
  photo: {
    width: 72,
    height: 90,
    borderRadius: 8,
    backgroundColor: COLORS.background,
  },
  photoPlaceholder: {
    width: 72,
    height: 90,
    borderRadius: 8,
    backgroundColor: '#e2e8f0',
    justifyContent: 'center',
    alignItems: 'center',
  },
  photoPlaceholderText: {
    fontSize: 24,
    fontWeight: '700',
    color: '#94a3b8',
  },
  infoColumn: {
    flex: 1,
  },
  nameRow: {
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
  activeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: '#fee2e2',
  },
  activeBadgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#b91c1c',
  },
  locatedBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: '#dcfce7',
  },
  locatedBadgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#15803d',
  },
  metaList: {
    gap: 2,
  },
  meta: {
    fontSize: 12,
    color: COLORS.secondary,
  },
  summary: {
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
  caseNumber: {
    fontSize: 12,
    color: COLORS.secondary,
    fontWeight: '600',
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
