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
import { CourtPersonMatch, CourtCase } from '../types';
import { courtApi } from '../services/api';
import { COLORS } from '../constants';

export default function CourtScreen() {
  const [name, setName] = useState('');
  const [caseNumber, setCaseNumber] = useState('');
  const [matches, setMatches] = useState<CourtPersonMatch[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [dataAsOf, setDataAsOf] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  const search = useCallback(async (isRefresh = false) => {
    if (!name.trim() && !caseNumber.trim()) {
      setMatches([]);
      setWarnings([]);
      setDataAsOf(null);
      setHasSearched(false);
      return;
    }
    if (!isRefresh) setLoading(true);
    setError(null);
    setHasSearched(true);
    try {
      const response = await courtApi.lookup({
        name: name.trim(),
        case_number: caseNumber.trim(),
        include_bookings: true,
        limit: 25,
      });
      setMatches(response.matches);
      setWarnings(response.warnings);
      setDataAsOf(response.data_as_of);
    } catch (err) {
      setError('Could not load court records. Pull down to retry.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [name, caseNumber]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (name.trim() || caseNumber.trim()) {
        search();
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [name, caseNumber, search]);

  const onRefresh = () => {
    setRefreshing(true);
    search(true);
  };

  const openUrl = (url: string) => {
    if (!url) return;
    Linking.openURL(url).catch((err) => console.error('Failed to open URL:', err));
  };

  const renderCase = (courtCase: CourtCase) => (
    <View style={styles.caseCard}>
      <View style={styles.caseHeader}>
        <Text style={styles.caseNumber}>{courtCase.case_number || 'Unknown case'}</Text>
        <View style={styles.statusBadge}>
          <Text style={styles.statusBadgeText}>{courtCase.status || 'Open'}</Text>
        </View>
      </View>
      <Text style={styles.courtName}>{courtCase.court_name}</Text>
      {courtCase.court_county ? <Text style={styles.meta}>{courtCase.court_county} County</Text> : null}
      {courtCase.filed_date ? <Text style={styles.meta}>Filed {courtCase.filed_date}</Text> : null}
      {courtCase.charges_text ? (
        <Text style={styles.charges} numberOfLines={4}>
          {courtCase.charges_text}
        </Text>
      ) : null}
      {courtCase.disposition ? (
        <View style={styles.dispositionBox}>
          <Text style={styles.dispositionLabel}>Disposition</Text>
          <Text style={styles.dispositionText}>{courtCase.disposition}</Text>
          {courtCase.sentence_text ? <Text style={styles.sentenceText}>{courtCase.sentence_text}</Text> : null}
          {courtCase.sentence_date ? <Text style={styles.sentenceDate}>{courtCase.sentence_date}</Text> : null}
        </View>
      ) : null}
      {courtCase.related_jail_bookings && courtCase.related_jail_bookings.length > 0 ? (
        <View style={styles.bookingsBox}>
          <Text style={styles.bookingsLabel}>Related jail bookings</Text>
          {courtCase.related_jail_bookings.map((booking) => (
            <Text key={booking.id} style={styles.bookingItem}>
              {booking.person_name} — {booking.county_name} ({booking.booking_at?.slice(0, 10) || 'Unknown date'})
            </Text>
          ))}
        </View>
      ) : null}
      {courtCase.source_url ? (
        <TouchableOpacity onPress={() => openUrl(courtCase.source_url)}>
          <Text style={styles.sourceLink}>Court source →</Text>
        </TouchableOpacity>
      ) : null}
    </View>
  );

  const renderMatch = ({ item }: { item: CourtPersonMatch }) => (
    <View style={styles.matchCard}>
      <View style={styles.matchHeader}>
        <Text style={styles.matchName}>{item.person.display_name || item.person.name}</Text>
        <View style={styles.confidenceBadge}>
          <Text style={styles.confidenceText}>{Math.round(item.confidence * 100)}% match</Text>
        </View>
      </View>
      {item.court_cases.map((courtCase, index) => (
        <View key={courtCase.id || index}>{renderCase(courtCase)}</View>
      ))}
    </View>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerEyebrow}>Case Status</Text>
        <Text style={styles.headerTitle}>Court Lookup</Text>
        <Text style={styles.headerSubtitle}>
          Search Montana criminal court cases by name or case number
        </Text>
      </View>
      <View style={styles.controls}>
        <TextInput
          style={styles.searchInput}
          placeholder="Person name..."
          value={name}
          onChangeText={setName}
          placeholderTextColor={COLORS.secondary}
          returnKeyType="search"
        />
        <TextInput
          style={[styles.searchInput, styles.caseInput]}
          placeholder="Or case number..."
          value={caseNumber}
          onChangeText={setCaseNumber}
          placeholderTextColor={COLORS.secondary}
          returnKeyType="search"
        />
        {warnings.length > 0 ? (
          <View style={styles.warningBox}>
            {warnings.map((warning, index) => (
              <Text key={index} style={styles.warningText}>• {warning}</Text>
            ))}
          </View>
        ) : null}
        {dataAsOf ? <Text style={styles.dataAsOf}>Data as of {dataAsOf.slice(0, 10)}</Text> : null}
      </View>
      {loading && !refreshing ? (
        <View style={styles.loader}>
          <ActivityIndicator size="large" color={COLORS.accent} />
        </View>
      ) : (
        <FlatList
          data={matches}
          keyExtractor={(item, index) => `match-${index}`}
          renderItem={renderMatch}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              {hasSearched ? (
                <Text style={styles.emptyText}>{error || 'No cases found.'}</Text>
              ) : (
                <>
                  <Text style={styles.emptyText}>Enter a name or case number to search court records.</Text>
                  <Text style={styles.emptySubtext}>Examples: "John Smith" or "CR-2024-1234"</Text>
                </>
              )}
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
  searchInput: {
    backgroundColor: COLORS.background,
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    color: COLORS.primary,
  },
  caseInput: {
    marginTop: 10,
  },
  warningBox: {
    marginTop: 12,
    padding: 10,
    backgroundColor: '#fff7ed',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#fed7aa',
  },
  warningText: {
    fontSize: 12,
    color: '#9a3412',
    lineHeight: 18,
  },
  dataAsOf: {
    marginTop: 10,
    fontSize: 11,
    color: COLORS.secondary,
    textTransform: 'uppercase',
    fontWeight: '700',
    letterSpacing: 0.6,
  },
  loader: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  list: {
    padding: 16,
  },
  matchCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  matchHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  matchName: {
    fontSize: 17,
    fontWeight: '700',
    color: COLORS.primary,
    flex: 1,
    marginRight: 8,
  },
  confidenceBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: '#eff6ff',
  },
  confidenceText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#1d4ed8',
  },
  caseCard: {
    paddingVertical: 12,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  caseHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 6,
  },
  caseNumber: {
    fontSize: 15,
    fontWeight: '700',
    color: COLORS.primary,
    flex: 1,
    marginRight: 8,
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    backgroundColor: '#f1f5f9',
  },
  statusBadgeText: {
    fontSize: 11,
    fontWeight: '600',
    color: COLORS.secondary,
  },
  courtName: {
    fontSize: 13,
    fontWeight: '600',
    color: COLORS.primary,
    marginBottom: 2,
  },
  meta: {
    fontSize: 12,
    color: COLORS.secondary,
    marginBottom: 2,
  },
  charges: {
    fontSize: 13,
    color: COLORS.primary,
    lineHeight: 18,
    marginTop: 8,
    marginBottom: 8,
  },
  dispositionBox: {
    backgroundColor: '#f0fdf4',
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#bbf7d0',
  },
  dispositionLabel: {
    fontSize: 10,
    fontWeight: '800',
    color: '#166534',
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 4,
  },
  dispositionText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#14532d',
  },
  sentenceText: {
    fontSize: 12,
    color: '#166534',
    marginTop: 4,
  },
  sentenceDate: {
    fontSize: 11,
    color: '#15803d',
    marginTop: 2,
  },
  bookingsBox: {
    backgroundColor: '#f8fafc',
    borderRadius: 8,
    padding: 10,
    marginBottom: 10,
  },
  bookingsLabel: {
    fontSize: 10,
    fontWeight: '800',
    color: COLORS.secondary,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 4,
  },
  bookingItem: {
    fontSize: 12,
    color: COLORS.primary,
    marginBottom: 2,
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
  emptySubtext: {
    color: COLORS.secondary,
    fontSize: 12,
    marginTop: 6,
  },
});
