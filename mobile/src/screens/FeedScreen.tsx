import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
  StyleSheet,
  TextInput,
  ScrollView,
} from 'react-native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useNavigation } from '@react-navigation/native';
import { api } from '../services/api';
import { County, Post, StatsResponse } from '../types';
import { COLORS } from '../constants';
import { usePremium } from '../context/PremiumContext';

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
};

export default function FeedScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<FeedStackParamList>>();
  const [posts, setPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [counties, setCounties] = useState<County[]>([]);
  const [selectedCounty, setSelectedCounty] = useState<string>('');
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState('');

  const [agencyType, setAgencyType] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  const { isPremium } = usePremium();

  const fetchPosts = useCallback(async (pageNum: number = 1, searchQuery: string = '', county: string = '', filters: { agency_type?: string; date_from?: string; date_to?: string } = {}) => {
    try {
      setError('');
      const data = await api.getPosts({
        page: pageNum,
        per_page: 20,
        search: searchQuery,
        county,
        agency_type: filters.agency_type,
        date_from: filters.date_from,
        date_to: filters.date_to,
      });
      if (pageNum === 1) {
        setPosts(data.posts);
      } else {
      setPosts(prev => [...prev, ...data.posts]);
      }
      setTotalPages(data.total_pages);
      setPage(pageNum);
    } catch (fetchError) {
      console.error('Failed to fetch posts:', fetchError);
      setError('Unable to load blotter posts right now.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let active = true;

    const loadBootstrapData = async () => {
      try {
        const [statsData, countiesData] = await Promise.all([
          api.getStats(),
          api.getCounties(),
        ]);

        if (!active) {
          return;
        }

        setStats(statsData);
        setCounties(countiesData.counties);
      } catch (bootstrapError) {
        console.error('Failed to load feed metadata:', bootstrapError);
      } finally {
        if (active) {
          setBootstrapping(false);
        }
      }
    };

    loadBootstrapData();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setSearch(query.trim());
    }, 350);

    return () => clearTimeout(timeoutId);
  }, [query]);

  useEffect(() => {
    setLoading(true);
    setPage(1);
    fetchPosts(1, search, selectedCounty, { agency_type: agencyType, date_from: dateFrom, date_to: dateTo });
  }, [search, selectedCounty, agencyType, dateFrom, dateTo, fetchPosts]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchPosts(1, search, selectedCounty, { agency_type: agencyType, date_from: dateFrom, date_to: dateTo });
  }, [search, selectedCounty, agencyType, dateFrom, dateTo, fetchPosts]);

  const onEndReached = useCallback(() => {
    if (page < totalPages && !loading) {
      const nextPage = page + 1;
      fetchPosts(nextPage, search, selectedCounty, { agency_type: agencyType, date_from: dateFrom, date_to: dateTo });
    }
  }, [page, totalPages, loading, search, selectedCounty, agencyType, dateFrom, dateTo, fetchPosts]);

  const formatCompactDate = (value?: string) => {
    if (!value) {
      return 'Unknown';
    }

    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }

    return parsed.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  const clearFilters = () => {
    setQuery('');
    setSearch('');
    setSelectedCounty('');
    setAgencyType('');
    setDateFrom('');
    setDateTo('');
    setShowFilters(false);
  };

  const renderPost = ({ item }: { item: Post }) => (
    <TouchableOpacity
      style={styles.card}
      onPress={() => navigation.navigate('PostDetail', { postId: item.id })}
    >
      <View style={styles.postHeader}>
        <Text style={styles.agencyType}>
          {item.agency_type === 'Sheriff' ? '👮' : '🚔'} {item.agency_type}
        </Text>
        <Text style={styles.date}>{formatCompactDate(item.incident_date)}</Text>
      </View>
      <Text style={styles.title} numberOfLines={2}>
        {item.title}
      </Text>
      <Text style={styles.summary} numberOfLines={3}>
        {item.summary}
      </Text>
      <View style={styles.postFooter}>
        <Text style={styles.county}>{item.county} County</Text>
        <Text style={styles.agency}>{item.agency_name}</Text>
      </View>
    </TouchableOpacity>
  );

  if (loading && posts.length === 0) {
    return (
      <View style={styles.centerContainer}>
        <ActivityIndicator size="large" color={COLORS.primary} />
        <Text style={styles.loadingText}>
          {bootstrapping ? 'Loading Montana Blotter...' : 'Loading blotter...'}
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.hero}>
        <Text style={styles.heroEyebrow}>MontanaBlotter.com</Text>
        <Text style={styles.heroTitle}>Statewide public safety feed</Text>
        <Text style={styles.heroSubtitle}>
          Browse recent blotter posts, narrow by county, and track the latest publishing activity from across Montana.
        </Text>
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <Text style={styles.statValue}>{stats?.total_posts ?? '...'}</Text>
            <Text style={styles.statLabel}>Posts</Text>
          </View>
          <View style={styles.statCard}>
            <Text style={styles.statValue}>{stats?.total_counties ?? '...'}</Text>
            <Text style={styles.statLabel}>Counties</Text>
          </View>
          <View style={styles.statCard}>
            <Text style={styles.statValue}>{stats?.total_agencies ?? '...'}</Text>
            <Text style={styles.statLabel}>Agencies</Text>
          </View>
        </View>
        {stats?.latest_blotter && (
          <View style={styles.latestBanner}>
            <Text style={styles.latestLabel}>Latest blotter</Text>
            <Text style={styles.latestValue}>
              {stats.latest_blotter.county} County · {formatCompactDate(stats.latest_blotter.upload_date)}
            </Text>
          </View>
        )}
      </View>
      <View style={styles.searchContainer}>
        <TextInput
          style={styles.searchInput}
          placeholder="Search incidents..."
          value={query}
          onChangeText={setQuery}
          placeholderTextColor={COLORS.secondary}
        />
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.filterRow}
        >
          <TouchableOpacity
            style={[styles.filterChip, !selectedCounty && styles.filterChipActive]}
            onPress={() => setSelectedCounty('')}
          >
            <Text style={[styles.filterChipText, !selectedCounty && styles.filterChipTextActive]}>
              All counties
            </Text>
          </TouchableOpacity>
          {counties.slice(0, 12).map((county) => {
            const active = county.county === selectedCounty;
            return (
              <TouchableOpacity
                key={county.county}
                style={[styles.filterChip, active && styles.filterChipActive]}
                onPress={() => setSelectedCounty(active ? '' : county.county)}
              >
                <Text style={[styles.filterChipText, active && styles.filterChipTextActive]}>
                  {county.county}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
        <View style={styles.resultsRow}>
          <Text style={styles.resultsText}>
            {selectedCounty ? `${selectedCounty} County` : 'All Montana counties'}
            {search ? ` · "${search}"` : ''}
            {agencyType ? ` · ${agencyType}` : ''}
            {dateFrom || dateTo ? ` · ${dateFrom || '...'} to ${dateTo || '...'}` : ''}
          </Text>
          {(selectedCounty || search || agencyType || dateFrom || dateTo) ? (
            <TouchableOpacity onPress={clearFilters}>
              <Text style={styles.clearFilters}>Clear</Text>
            </TouchableOpacity>
          ) : null}
        </View>
        <TouchableOpacity style={styles.advancedToggle} onPress={() => setShowFilters(!showFilters)}>
          <Text style={styles.advancedToggleText}>
            {showFilters ? '▲ Hide advanced filters' : '▼ Advanced filters'}
          </Text>
        </TouchableOpacity>
        {showFilters && (
          <View style={styles.advancedPanel}>
            {!isPremium && (
              <View style={styles.premiumOverlay}>
                <Text style={styles.premiumOverlayText}>🔒 Advanced filters are a Premium feature</Text>
                <TouchableOpacity
                  style={styles.premiumOverlayButton}
                  onPress={() => (navigation as any).navigate('Premium')}
                >
                  <Text style={styles.premiumOverlayButtonText}>Upgrade to Premium</Text>
                </TouchableOpacity>
              </View>
            )}
            <Text style={styles.advancedLabel}>Agency Type</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
              {['', 'Sheriff', 'Police', 'Highway Patrol'].map((type) => {
                const active = agencyType === type;
                return (
                  <TouchableOpacity
                    key={type || 'all'}
                    style={[styles.filterChip, active && styles.filterChipActive]}
                    onPress={() => setAgencyType(active ? '' : type)}
                    disabled={!isPremium}
                  >
                    <Text style={[styles.filterChipText, active && styles.filterChipTextActive]}>
                      {type || 'All types'}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
            <Text style={styles.advancedLabel}>Date Range</Text>
            <View style={styles.dateRow}>
              <TextInput
                style={[styles.searchInput, styles.dateInput]}
                placeholder="From (YYYY-MM-DD)"
                value={dateFrom}
                onChangeText={setDateFrom}
                placeholderTextColor={COLORS.secondary}
                editable={isPremium}
              />
              <TextInput
                style={[styles.searchInput, styles.dateInput]}
                placeholder="To (YYYY-MM-DD)"
                value={dateTo}
                onChangeText={setDateTo}
                placeholderTextColor={COLORS.secondary}
                editable={isPremium}
              />
            </View>
          </View>
        )}
      </View>
      {error ? (
        <View style={styles.errorBanner}>
          <Text style={styles.errorBannerText}>{error}</Text>
        </View>
      ) : null}
      <FlatList
        data={posts}
        keyExtractor={item => String(item.id)}
        renderItem={renderPost}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
        onEndReached={onEndReached}
        onEndReachedThreshold={0.5}
        ListFooterComponent={
          loading && posts.length > 0 ? (
            <ActivityIndicator size="small" color={COLORS.primary} />
          ) : null
        }
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No posts found</Text>
            <Text style={styles.emptySubtext}>
              Try a different search or switch counties.
            </Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
  },
  loadingText: {
    marginTop: 12,
    color: COLORS.secondary,
    fontSize: 14,
  },
  hero: {
    backgroundColor: COLORS.primary,
    paddingHorizontal: 16,
    paddingTop: 18,
    paddingBottom: 20,
  },
  heroEyebrow: {
    color: '#f97316',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  heroTitle: {
    color: COLORS.card,
    fontSize: 26,
    fontWeight: '800',
    marginBottom: 8,
  },
  heroSubtitle: {
    color: '#cbd5e1',
    fontSize: 14,
    lineHeight: 20,
  },
  statsRow: {
    flexDirection: 'row',
    marginTop: 18,
    gap: 10,
  },
  statCard: {
    flex: 1,
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 14,
    paddingVertical: 12,
    paddingHorizontal: 10,
  },
  statValue: {
    color: COLORS.card,
    fontSize: 20,
    fontWeight: '800',
    marginBottom: 4,
  },
  statLabel: {
    color: '#cbd5e1',
    fontSize: 12,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  latestBanner: {
    marginTop: 14,
    padding: 12,
    borderRadius: 12,
    backgroundColor: 'rgba(249,115,22,0.12)',
    borderWidth: 1,
    borderColor: 'rgba(249,115,22,0.25)',
  },
  latestLabel: {
    color: '#fdba74',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  latestValue: {
    color: COLORS.card,
    fontSize: 14,
    fontWeight: '600',
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
  filterRow: {
    paddingTop: 12,
    paddingBottom: 4,
    gap: 8,
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: COLORS.background,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  filterChipActive: {
    backgroundColor: COLORS.primary,
    borderColor: COLORS.primary,
  },
  filterChipText: {
    color: COLORS.secondary,
    fontSize: 13,
    fontWeight: '600',
  },
  filterChipTextActive: {
    color: COLORS.card,
  },
  resultsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 10,
  },
  resultsText: {
    flex: 1,
    color: COLORS.secondary,
    fontSize: 12,
    marginRight: 12,
  },
  clearFilters: {
    color: COLORS.accent,
    fontSize: 12,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  errorBanner: {
    marginHorizontal: 16,
    marginTop: 12,
    padding: 12,
    borderRadius: 10,
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
  },
  errorBannerText: {
    color: COLORS.error,
    fontSize: 13,
    fontWeight: '600',
  },
  advancedToggle: {
    marginTop: 10,
    paddingVertical: 8,
    alignItems: 'center',
  },
  advancedToggleText: {
    fontSize: 13,
    fontWeight: '700',
    color: COLORS.accent,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  advancedPanel: {
    marginTop: 10,
    padding: 12,
    backgroundColor: COLORS.background,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
    position: 'relative',
  },
  advancedLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: COLORS.secondary,
    textTransform: 'uppercase',
    marginTop: 10,
    marginBottom: 6,
  },
  dateRow: {
    flexDirection: 'row',
    gap: 8,
  },
  dateInput: {
    flex: 1,
  },
  premiumOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(248,250,252,0.92)',
    borderRadius: 10,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
    padding: 16,
  },
  premiumOverlayText: {
    fontSize: 15,
    fontWeight: '700',
    color: COLORS.primary,
    textAlign: 'center',
    marginBottom: 12,
  },
  premiumOverlayButton: {
    backgroundColor: COLORS.accent,
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 18,
  },
  premiumOverlayButtonText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '700',
  },
  list: {
    padding: 16,
  },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 2,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  postHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  agencyType: {
    fontSize: 12,
    fontWeight: '600',
    color: COLORS.secondary,
    textTransform: 'uppercase',
  },
  date: {
    fontSize: 12,
    color: COLORS.secondary,
  },
  title: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    marginBottom: 8,
    lineHeight: 22,
  },
  summary: {
    fontSize: 14,
    color: COLORS.secondary,
    lineHeight: 20,
    marginBottom: 12,
  },
  postFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    paddingTop: 12,
  },
  county: {
    fontSize: 12,
    fontWeight: '600',
    color: COLORS.accent,
  },
  agency: {
    fontSize: 12,
    color: COLORS.secondary,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingTop: 50,
  },
  emptyText: {
    fontSize: 16,
    color: COLORS.secondary,
    fontWeight: '600',
  },
  emptySubtext: {
    marginTop: 6,
    fontSize: 13,
    color: COLORS.secondary,
  },
});
