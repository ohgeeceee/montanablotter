import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  Linking,
  TextInput,
  StyleSheet,
  RefreshControl,
  ActivityIndicator,
} from 'react-native';
import { JailBooking, JailBookingsResponse, JailRoster } from '../types';
import { jailApi } from '../services/api';
import { COLORS } from '../constants';

const JAIL_ROSTERS: JailRoster[] = [
  { name: 'Beaverhead', url: 'https://beaverheadcountymt.gov/departments/sheriff/', phone: '406-683-3700', hasOnline: true },
  { name: 'Big Horn', url: 'https://www.bighorncountymt.gov/239/Detention', phone: '406-665-9780', hasOnline: true },
  { name: 'Blaine', url: null, phone: '406-357-3260', hasOnline: false },
  { name: 'Broadwater', url: 'https://www.broadwatercountysheriff.org/roster.php', phone: '406-266-3445', hasOnline: true },
  { name: 'Carbon', url: 'https://carbonmt.gov/sheriff/', phone: '406-446-1234', hasOnline: true },
  { name: 'Carter', url: null, phone: '406-775-8741', hasOnline: false },
  { name: 'Cascade', url: 'https://www.cascadecountymt.gov/314/Inmate-Roster', phone: '406-454-6840', hasOnline: true },
  { name: 'Chouteau', url: null, phone: '406-622-3660', hasOnline: false },
  { name: 'Custer', url: null, phone: '406-874-3300', hasOnline: false },
  { name: 'Daniels', url: null, phone: '406-487-2691', hasOnline: false },
  { name: 'Dawson', url: 'https://www.dawsoncountymontana.com/sheriff', phone: '406-377-7600', hasOnline: true },
  { name: 'Deer Lodge', url: null, phone: '406-563-5421', hasOnline: false },
  { name: 'Fallon', url: null, phone: '406-778-2879', hasOnline: false },
  { name: 'Fergus', url: 'https://fergusmt.gov/detention-center-roster', phone: '406-535-3860', hasOnline: true },
  { name: 'Flathead', url: 'https://apps.flathead.mt.gov/jailroster/', phone: '406-758-5610', hasOnline: true },
  { name: 'Gallatin', url: 'https://gallatin-so-mt.zuercherportal.com/#/inmates', phone: '406-582-2100', hasOnline: true },
  { name: 'Garfield', url: null, phone: '406-557-2540', hasOnline: false },
  { name: 'Glacier', url: 'https://glaciercountymt.gov/category/jail-roster/', phone: '406-873-4600', hasOnline: true },
  { name: 'Golden Valley', url: null, phone: '406-568-2321', hasOnline: false },
  { name: 'Granite', url: 'https://granitecountyjail.org/', phone: '406-859-3771', hasOnline: true },
  { name: 'Hill', url: null, phone: '406-265-5481', hasOnline: false },
  { name: 'Jefferson', url: 'https://jefferson-so-mt.zuercherportal.com/#/inmates', phone: '406-225-4075', hasOnline: true },
  { name: 'Judith Basin', url: null, phone: '406-535-3860', hasOnline: false },
  { name: 'Lake', url: null, phone: '406-883-7301', hasOnline: false },
  { name: 'Lewis and Clark', url: 'https://www.lccountymt.gov/Sheriff/Detention-Center', phone: '406-447-8270', hasOnline: true },
  { name: 'Liberty', url: null, phone: '406-759-5171', hasOnline: false },
  { name: 'Lincoln', url: null, phone: '406-293-0242', hasOnline: false },
  { name: 'Madison', url: null, phone: '406-843-5351', hasOnline: false },
  { name: 'McCone', url: null, phone: '406-485-3405', hasOnline: false },
  { name: 'Meagher', url: null, phone: '406-547-3397', hasOnline: false },
  { name: 'Mineral', url: 'https://co.mineral.mt.us/departments/sheriff/', phone: '406-822-3534', hasOnline: true },
  { name: 'Missoula', url: 'https://webapps.missoulacounty.us/jailroster/Inmates', phone: '406-258-4780', hasOnline: true },
  { name: 'Musselshell', url: null, phone: '406-323-1122', hasOnline: false },
  { name: 'Park', url: 'https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/', phone: '406-222-4172', hasOnline: true },
  { name: 'Petroleum', url: null, phone: '406-429-6551', hasOnline: false },
  { name: 'Phillips', url: 'https://phillipscosheriff.com/inmates/', phone: '406-654-2020', hasOnline: true },
  { name: 'Pondera', url: 'https://ponderacountyjail.org/inmate-search/', phone: '406-271-4100', hasOnline: true },
  { name: 'Powder River', url: null, phone: '406-436-2260', hasOnline: false },
  { name: 'Powell', url: 'https://www.powellcountymt.gov/sheriff/page/detention-facility', phone: '406-846-2711', hasOnline: true },
  { name: 'Prairie', url: null, phone: '406-635-5738', hasOnline: false },
  { name: 'Ravalli', url: 'https://ravallicounty.gov/239/Adult-Detention-Center', phone: '406-375-4060', hasOnline: true },
  { name: 'Richland', url: null, phone: '406-433-2919', hasOnline: false },
  { name: 'Roosevelt', url: null, phone: '406-653-6230', hasOnline: false },
  { name: 'Rosebud', url: null, phone: '406-346-2715', hasOnline: false },
  { name: 'Sanders', url: 'https://sanders-mt.publiclogs.com/', phone: '406-827-3584', hasOnline: true },
  { name: 'Sheridan', url: null, phone: '406-765-1200', hasOnline: false },
  { name: 'Silver Bow', url: 'https://co.silverbow.mt.us/3274/Detention-Center', phone: '406-497-1120', hasOnline: true },
  { name: 'Stillwater', url: null, phone: '406-322-5326', hasOnline: false },
  { name: 'Sweet Grass', url: null, phone: '406-932-5143', hasOnline: false },
  { name: 'Teton', url: null, phone: '406-466-5781', hasOnline: false },
  { name: 'Toole', url: null, phone: '406-434-5585', hasOnline: false },
  { name: 'Treasure', url: null, phone: '406-342-5211', hasOnline: false },
  { name: 'Valley', url: 'https://www.valleycountymt.gov/1288/Jail-Roster', phone: '406-228-9355', hasOnline: true },
  { name: 'Wheatland', url: null, phone: '406-632-4311', hasOnline: false },
  { name: 'Wibaux', url: null, phone: '406-796-2415', hasOnline: false },
  { name: 'Yellowstone', url: 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp', phone: '406-256-2929', hasOnline: true },
];

const STATUS_FILTERS: { key: 'current' | 'recent' | 'released' | 'all'; label: string }[] = [
  { key: 'current', label: 'Current' },
  { key: 'recent', label: '24h' },
  { key: 'released', label: 'Released' },
  { key: 'all', label: 'All' },
];

type TabKey = 'live' | 'directory';

export default function JailRostersScreen() {
  const [activeTab, setActiveTab] = useState<TabKey>('live');
  const [directorySearch, setDirectorySearch] = useState('');
  const [bookings, setBookings] = useState<JailBooking[]>([]);
  const [summary, setSummary] = useState<JailBookingsResponse['summary'] | null>(null);
  const [statusFilter, setStatusFilter] = useState<'current' | 'recent' | 'released' | 'all'>('current');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadBookings = useCallback(async (isRefresh = false) => {
    if (!isRefresh) setLoading(true);
    setError(null);
    try {
      const response = await jailApi.getBookings({
        status: statusFilter,
        q: q.trim(),
      });
      setBookings(response.bookings);
      setSummary(response.summary);
    } catch (err) {
      setError('Could not load jail bookings. Pull down to retry.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [statusFilter, q]);

  useEffect(() => {
    if (activeTab === 'live') {
      loadBookings();
    }
  }, [activeTab, statusFilter, q, loadBookings]);

  const onRefresh = () => {
    setRefreshing(true);
    loadBookings(true);
  };

  const filteredRosters = JAIL_ROSTERS.filter(roster =>
    roster.name.toLowerCase().includes(directorySearch.toLowerCase())
  );
  const onlineCount = JAIL_ROSTERS.filter((roster) => roster.hasOnline).length;
  const phoneOnlyCount = JAIL_ROSTERS.length - onlineCount;

  const openUrl = (url: string) => {
    Linking.openURL(url).catch(err => console.error('Failed to open URL:', err));
  };

  const callPhone = (phone: string) => {
    Linking.openURL(`tel:${phone}`).catch(err => console.error('Failed to make call:', err));
  };

  const renderBooking = ({ item }: { item: JailBooking }) => (
    <View style={styles.bookingCard}>
      <View style={styles.bookingHeader}>
        <Text style={styles.bookingName}>{item.person_name || 'Unknown'}</Text>
        <View style={[styles.badge, item.is_current ? styles.badgeCurrent : styles.badgeReleased]}>
          <Text style={[styles.badgeText, item.is_current ? styles.badgeTextCurrent : styles.badgeTextReleased]}>
            {item.booking_status_label || item.booking_status}
          </Text>
        </View>
      </View>
      <View style={styles.bookingMetaRow}>
        <Text style={styles.bookingMeta}>{item.county_name} County</Text>
        {item.age ? <Text style={styles.bookingMeta}>• Age {item.age}</Text> : null}
        {item.is_new_24h ? <Text style={styles.newBadge}>NEW</Text> : null}
      </View>
      {item.charges_summary ? (
        <Text style={styles.chargesText} numberOfLines={3}>
          {item.charges_summary}
        </Text>
      ) : null}
      <View style={styles.bookingFooter}>
        <Text style={styles.bookingTime}>{item.booking_at_label || item.booking_at || ''}</Text>
        {item.source_url ? (
          <TouchableOpacity onPress={() => openUrl(item.source_url!)}>
            <Text style={styles.sourceLink}>Official roster →</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    </View>
  );

  const renderRoster = ({ item }: { item: JailRoster }) => (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.countyName}>{item.name} County</Text>
        <View style={[styles.badge, item.hasOnline ? styles.badgeOnline : styles.badgePhone]}>
          <Text style={[styles.badgeText, item.hasOnline ? styles.badgeTextOnline : styles.badgeTextPhone]}>
            {item.hasOnline ? 'Online' : 'Phone Only'}
          </Text>
        </View>
      </View>
      <Text style={styles.subtext}>{item.name} County Sheriff's Office</Text>
      <View style={styles.buttonContainer}>
        <TouchableOpacity
          style={[styles.button, item.hasOnline ? styles.buttonPrimary : styles.buttonSecondary]}
          onPress={() => openUrl(item.url || 'https://vinelink.vineapps.com/state/mt')}
        >
          <Text style={[styles.buttonText, item.hasOnline ? styles.buttonTextPrimary : styles.buttonTextSecondary]}>
            {item.hasOnline ? 'View Roster' : 'VINELink'}
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.phoneButton}
          onPress={() => callPhone(item.phone)}
        >
          <Text style={styles.phoneButtonText}>📞 {item.phone}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );

  const renderEmptyBookings = () => (
    <View style={styles.emptyContainer}>
      {error ? (
        <Text style={styles.emptyText}>{error}</Text>
      ) : (
        <>
          <Text style={styles.emptyText}>No bookings matched your filters.</Text>
          <Text style={styles.emptySubtext}>Try a different status or search term.</Text>
        </>
      )}
    </View>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerEyebrow}>County Directory</Text>
        <Text style={styles.headerTitle}>Montana Jail Rosters</Text>
        <Text style={styles.headerSubtitle}>
          Live bookings and links to all 56 county jail rosters
        </Text>
        <View style={styles.tabRow}>
          <TouchableOpacity
            style={[styles.tab, activeTab === 'live' && styles.tabActive]}
            onPress={() => setActiveTab('live')}
          >
            <Text style={[styles.tabText, activeTab === 'live' && styles.tabTextActive]}>Live Bookings</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.tab, activeTab === 'directory' && styles.tabActive]}
            onPress={() => setActiveTab('directory')}
          >
            <Text style={[styles.tabText, activeTab === 'directory' && styles.tabTextActive]}>Directory</Text>
          </TouchableOpacity>
        </View>
      </View>

      {activeTab === 'live' ? (
        <>
          <View style={styles.liveHeader}>
            <View style={styles.summaryRow}>
              {summary ? (
                <>
                  <View style={styles.summaryItem}>
                    <Text style={styles.summaryValue}>{summary.current_bookings}</Text>
                    <Text style={styles.summaryLabel}>Current</Text>
                  </View>
                  <View style={styles.summaryItem}>
                    <Text style={styles.summaryValue}>{summary.new_24h}</Text>
                    <Text style={styles.summaryLabel}>New 24h</Text>
                  </View>
                  <View style={styles.summaryItem}>
                    <Text style={styles.summaryValue}>{summary.tracked_counties}</Text>
                    <Text style={styles.summaryLabel}>Counties</Text>
                  </View>
                </>
              ) : (
                <ActivityIndicator color={COLORS.accent} />
              )}
            </View>
            <View style={styles.filterRow}>
              {STATUS_FILTERS.map((filter) => (
                <TouchableOpacity
                  key={filter.key}
                  style={[styles.filterChip, statusFilter === filter.key && styles.filterChipActive]}
                  onPress={() => setStatusFilter(filter.key)}
                >
                  <Text style={[styles.filterChipText, statusFilter === filter.key && styles.filterChipTextActive]}>
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
          </View>
          {loading && !refreshing ? (
            <View style={styles.loader}>
              <ActivityIndicator size="large" color={COLORS.accent} />
            </View>
          ) : (
            <FlatList
              data={bookings}
              keyExtractor={item => `booking-${item.id}`}
              renderItem={renderBooking}
              contentContainerStyle={styles.list}
              showsVerticalScrollIndicator={false}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
              ListEmptyComponent={renderEmptyBookings}
            />
          )}
        </>
      ) : (
        <>
          <View style={styles.searchContainer}>
            <TextInput
              style={styles.searchInput}
              placeholder="Search county..."
              value={directorySearch}
              onChangeText={setDirectorySearch}
              placeholderTextColor={COLORS.secondary}
            />
            <Text style={styles.resultsText}>
              Showing {filteredRosters.length} of {JAIL_ROSTERS.length} counties
            </Text>
          </View>
          <FlatList
            data={filteredRosters}
            keyExtractor={item => item.name}
            renderItem={renderRoster}
            contentContainerStyle={styles.list}
            showsVerticalScrollIndicator={false}
            ListHeaderComponent={
              <View style={styles.stateLinks}>
                <TouchableOpacity
                  style={styles.stateLinkCard}
                  onPress={() => openUrl('https://offendersearch.mt.gov/conweb/')}
                >
                  <Text style={styles.stateLinkTitle}>MT DOC Search</Text>
                  <Text style={styles.stateLinkSubtitle}>State offender lookup</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.stateLinkCard}
                  onPress={() => openUrl('https://vinelink.vineapps.com/state/mt')}
                >
                  <Text style={styles.stateLinkTitle}>VINELink</Text>
                  <Text style={styles.stateLinkSubtitle}>Statewide custody alerts</Text>
                </TouchableOpacity>
              </View>
            }
            ListEmptyComponent={
              <View style={styles.emptyContainer}>
                <Text style={styles.emptyText}>No counties matched that search.</Text>
              </View>
            }
          />
        </>
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
    marginBottom: 16,
  },
  tabRow: {
    flexDirection: 'row',
    gap: 10,
  },
  tab: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    backgroundColor: 'rgba(255,255,255,0.08)',
    alignItems: 'center',
  },
  tabActive: {
    backgroundColor: COLORS.accent,
  },
  tabText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#cbd5e1',
  },
  tabTextActive: {
    color: COLORS.card,
  },
  liveHeader: {
    padding: 16,
    backgroundColor: COLORS.card,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  summaryRow: {
    flexDirection: 'row',
    gap: 16,
    marginBottom: 14,
  },
  summaryItem: {
    minWidth: 70,
  },
  summaryValue: {
    fontSize: 22,
    fontWeight: '800',
    color: COLORS.primary,
  },
  summaryLabel: {
    fontSize: 11,
    color: COLORS.secondary,
    textTransform: 'uppercase',
    fontWeight: '700',
    letterSpacing: 0.6,
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
  searchContainer: {
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
  stateLinks: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 16,
  },
  stateLinkCard: {
    flex: 1,
    padding: 14,
    borderRadius: 12,
    backgroundColor: '#eff6ff',
    borderWidth: 1,
    borderColor: '#bfdbfe',
  },
  stateLinkTitle: {
    color: '#1d4ed8',
    fontSize: 13,
    fontWeight: '700',
    marginBottom: 4,
  },
  stateLinkSubtitle: {
    color: '#1e40af',
    fontSize: 12,
    lineHeight: 17,
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
    alignItems: 'center',
    marginBottom: 4,
  },
  countyName: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  badgeOnline: {
    backgroundColor: '#dcfce7',
  },
  badgePhone: {
    backgroundColor: '#f1f5f9',
  },
  badgeCurrent: {
    backgroundColor: '#dcfce7',
  },
  badgeReleased: {
    backgroundColor: '#f1f5f9',
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
  },
  badgeTextOnline: {
    color: '#15803d',
  },
  badgeTextPhone: {
    color: COLORS.secondary,
  },
  badgeTextCurrent: {
    color: '#15803d',
  },
  badgeTextReleased: {
    color: COLORS.secondary,
  },
  subtext: {
    fontSize: 12,
    color: COLORS.secondary,
    marginBottom: 12,
  },
  buttonContainer: {
    flexDirection: 'row',
    gap: 8,
  },
  button: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
  },
  buttonPrimary: {
    backgroundColor: COLORS.accent,
  },
  buttonSecondary: {
    backgroundColor: '#e2e8f0',
  },
  buttonText: {
    fontSize: 14,
    fontWeight: '600',
  },
  buttonTextPrimary: {
    color: COLORS.card,
  },
  buttonTextSecondary: {
    color: COLORS.primary,
  },
  phoneButton: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  phoneButtonText: {
    fontSize: 13,
    fontWeight: '600',
    color: COLORS.secondary,
  },
  bookingCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  bookingHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 8,
  },
  bookingName: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    flex: 1,
    marginRight: 8,
  },
  bookingMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  bookingMeta: {
    fontSize: 12,
    color: COLORS.secondary,
    fontWeight: '600',
  },
  newBadge: {
    fontSize: 10,
    fontWeight: '800',
    color: '#c2410c',
    backgroundColor: '#ffedd5',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    overflow: 'hidden',
  },
  chargesText: {
    fontSize: 13,
    color: COLORS.primary,
    lineHeight: 18,
    marginBottom: 10,
  },
  bookingFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    paddingTop: 10,
  },
  bookingTime: {
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
  emptySubtext: {
    color: COLORS.secondary,
    fontSize: 12,
    marginTop: 6,
  },
});
