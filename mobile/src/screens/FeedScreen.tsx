import React, { useState, useEffect, useCallback, memo } from 'react';
import {
  View,
  Text,
  FlatList,
  TextInput,
  ScrollView,
  ActivityIndicator,
  RefreshControl,
  StyleSheet,
  Pressable,
} from 'react-native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useNavigation } from '@react-navigation/native';
import { api } from '../services/api';
import { County, Post, StatsResponse } from '../types';
import { usePremium } from '../context/PremiumContext';
import { Badge, EmptyState, LoadingState, PressableCard, ScreenHeader } from '../components/ui';
import { colors, palette, radii, shadows, spacing, typography } from '../theme';

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
};

const PostCard = memo(({ item, onPress }: { item: Post; onPress: () => void }) => {
  const formatDate = (value?: string) => {
    if (!value) return 'Unknown';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  };

  return (
    <PressableCard onPress={onPress}>
      <View style={postStyles.header}>
        <Badge
          label={item.agency_type || 'Agency'}
          variant={item.agency_type === 'Sheriff' ? 'default' : 'muted'}
        />
        <Text style={postStyles.date}>{formatDate(item.incident_date)}</Text>
      </View>
      <Text style={postStyles.title} numberOfLines={2}>
        {item.title}
      </Text>
      <Text style={postStyles.summary} numberOfLines={3}>
        {item.summary}
      </Text>
      <View style={postStyles.footer}>
        <Text style={postStyles.county}>{item.county} County</Text>
        <Text style={postStyles.agency}>{item.agency_name}</Text>
      </View>
    </PressableCard>
  );
});

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
  const [selectedCounty, setSelectedCounty] = useState('');
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [error, setError] = useState('');
  const [agencyType, setAgencyType] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const { isPremium } = usePremium();

  const fetchPosts = useCallback(
    async (
      pageNum: number = 1,
      searchQuery: string = '',
      county: string = '',
      filters: { agency_type?: string; date_from?: string; date_to?: string } = {}
    ) => {
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
        setPosts((prev) => (pageNum === 1 ? data.posts : [...prev, ...data.posts]));
        setTotalPages(data.total_pages);
        setPage(pageNum);
      } catch (fetchError) {
        console.error('Failed to fetch posts:', fetchError);
        setError('Unable to load blotter posts right now.');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    []
  );

  useEffect(() => {
    let active = true;
    const loadBootstrapData = async () => {
      try {
        const [statsData, countiesData] = await Promise.all([api.getStats(), api.getCounties()]);
        if (!active) return;
        setStats(statsData);
        setCounties(countiesData.counties);
      } catch (bootstrapError) {
        console.error('Failed to load feed metadata:', bootstrapError);
      } finally {
        if (active) setBootstrapping(false);
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
      fetchPosts(page + 1, search, selectedCounty, { agency_type: agencyType, date_from: dateFrom, date_to: dateTo });
    }
  }, [page, totalPages, loading, search, selectedCounty, agencyType, dateFrom, dateTo, fetchPosts]);

  const clearFilters = () => {
    setQuery('');
    setSearch('');
    setSelectedCounty('');
    setAgencyType('');
    setDateFrom('');
    setDateTo('');
    setShowFilters(false);
  };

  const hasActiveFilters = selectedCounty || search || agencyType || dateFrom || dateTo;

  const renderPost = ({ item }: { item: Post }) => (
    <PostCard item={item} onPress={() => navigation.navigate('PostDetail', { postId: item.id })} />
  );

  const formatDate = (value?: string) => {
    if (!value) return 'Unknown';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  };

  if (loading && posts.length === 0) {
    return <LoadingState message={bootstrapping ? 'Loading Montana Blotter...' : 'Loading blotter...'} />;
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={posts}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderPost}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        onEndReached={onEndReached}
        onEndReachedThreshold={0.5}
        ListHeaderComponent={
          <View>
            <ScreenHeader
              eyebrow="MontanaBlotter.com"
              title="Statewide public safety feed"
              subtitle="Browse recent blotter posts, narrow by county, and track the latest publishing activity from across Montana."
              variant="dark"
            >
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
                    {stats.latest_blotter.county} County · {formatDate(stats.latest_blotter.upload_date)}
                  </Text>
                </View>
              )}
            </ScreenHeader>

            <View style={styles.searchContainer}>
              <TextInput
                style={styles.searchInput}
                placeholder="Search incidents..."
                value={query}
                onChangeText={setQuery}
                placeholderTextColor={colors.textMuted}
                accessibilityLabel="Search incidents"
              />
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.filterRow}
              >
                <Pressable
                  style={[styles.filterChip, !selectedCounty && styles.filterChipActive]}
                  onPress={() => setSelectedCounty('')}
                  android_ripple={{ color: 'rgba(0,0,0,0.06)', borderless: true }}
                >
                  <Text style={[styles.filterChipText, !selectedCounty && styles.filterChipTextActive]}>
                    All counties
                  </Text>
                </Pressable>
                {counties.slice(0, 12).map((county) => {
                  const active = county.county === selectedCounty;
                  return (
                    <Pressable
                      key={county.county}
                      style={[styles.filterChip, active && styles.filterChipActive]}
                      onPress={() => setSelectedCounty(active ? '' : county.county)}
                      android_ripple={{ color: 'rgba(0,0,0,0.06)', borderless: true }}
                    >
                      <Text style={[styles.filterChipText, active && styles.filterChipTextActive]}>
                        {county.county}
                      </Text>
                    </Pressable>
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
                {hasActiveFilters ? (
                  <Pressable
                    onPress={clearFilters}
                    android_ripple={{ color: 'rgba(0,0,0,0.06)', borderless: true }}
                    hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
                  >
                    <Text style={styles.clearFilters}>Clear</Text>
                  </Pressable>
                ) : null}
              </View>
              <Pressable
                style={styles.advancedToggle}
                onPress={() => setShowFilters(!showFilters)}
                android_ripple={{ color: 'rgba(0,0,0,0.04)', borderless: true }}
              >
                <Text style={styles.advancedToggleText}>
                  {showFilters ? '▲ Hide advanced filters' : '▼ Advanced filters'}
                </Text>
              </Pressable>
              {showFilters && (
                <View style={styles.advancedPanel}>
                  {!isPremium && (
                    <View style={styles.premiumOverlay}>
                      <Text style={styles.premiumOverlayText}>🔒 Advanced filters are a Premium feature</Text>
                      <Pressable
                        style={styles.premiumOverlayButton}
                        onPress={() => (navigation as any).navigate('Premium')}
                        android_ripple={{ color: 'rgba(255,255,255,0.2)', foreground: true }}
                      >
                        <Text style={styles.premiumOverlayButtonText}>Upgrade to Premium</Text>
                      </Pressable>
                    </View>
                  )}
                  <Text style={styles.advancedLabel}>Agency Type</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
                    {['', 'Sheriff', 'Police', 'Highway Patrol'].map((type) => {
                      const active = agencyType === type;
                      return (
                        <Pressable
                          key={type || 'all'}
                          style={[styles.filterChip, active && styles.filterChipActive]}
                          onPress={() => setAgencyType(active ? '' : type)}
                          disabled={!isPremium}
                          android_ripple={{ color: 'rgba(0,0,0,0.06)', borderless: true }}
                        >
                          <Text style={[styles.filterChipText, active && styles.filterChipTextActive]}>
                            {type || 'All types'}
                          </Text>
                        </Pressable>
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
                      placeholderTextColor={colors.textMuted}
                      editable={isPremium}
                      accessibilityLabel="Date from"
                    />
                    <TextInput
                      style={[styles.searchInput, styles.dateInput]}
                      placeholder="To (YYYY-MM-DD)"
                      value={dateTo}
                      onChangeText={setDateTo}
                      placeholderTextColor={colors.textMuted}
                      editable={isPremium}
                      accessibilityLabel="Date to"
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
          </View>
        }
        ListFooterComponent={
          loading && posts.length > 0 ? <ActivityIndicator size="small" color={colors.accent} style={styles.footerLoader} /> : null
        }
        ListEmptyComponent={
          <EmptyState
            icon="📭"
            title="No posts found"
            subtitle="Try a different search or switch counties."
          />
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  list: {
    paddingBottom: spacing[6],
  },
  statsRow: {
    flexDirection: 'row',
    marginTop: spacing[5],
    gap: spacing[3],
  },
  statCard: {
    flex: 1,
    backgroundColor: colors.glassLight,
    borderWidth: 1,
    borderColor: colors.glassBorder,
    borderRadius: radii.xl,
    paddingVertical: spacing[3],
    paddingHorizontal: spacing[3],
  },
  statValue: {
    color: colors.textInverse,
    fontSize: typography.sizes['2xl'],
    fontWeight: typography.weights.extrabold,
    marginBottom: spacing[1],
  },
  statLabel: {
    color: '#cbd5e1',
    fontSize: typography.sizes.xs,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    fontWeight: typography.weights.bold,
  },
  latestBanner: {
    marginTop: spacing[4],
    padding: spacing[3],
    borderRadius: radii.lg,
    backgroundColor: 'rgba(249,115,22,0.12)',
    borderWidth: 1,
    borderColor: 'rgba(249,115,22,0.25)',
  },
  latestLabel: {
    color: '#fdba74',
    fontSize: typography.sizes.xs,
    fontWeight: typography.weights.bold,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: spacing[1],
  },
  latestValue: {
    color: colors.textInverse,
    fontSize: typography.sizes.base,
    fontWeight: typography.weights.semibold,
  },
  searchContainer: {
    padding: spacing[4],
    backgroundColor: colors.card,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  searchInput: {
    backgroundColor: colors.background,
    borderRadius: radii.md,
    padding: spacing[3],
    fontSize: typography.sizes.md,
    color: colors.text,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterRow: {
    paddingTop: spacing[3],
    paddingBottom: spacing[1],
    gap: spacing[2],
  },
  filterChip: {
    paddingHorizontal: spacing[3],
    paddingVertical: spacing[2],
    borderRadius: radii.full,
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
  },
  filterChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  filterChipText: {
    color: colors.textMuted,
    fontSize: typography.sizes.sm,
    fontWeight: typography.weights.semibold,
  },
  filterChipTextActive: {
    color: colors.textInverse,
  },
  resultsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: spacing[3],
  },
  resultsText: {
    flex: 1,
    color: colors.textMuted,
    fontSize: typography.sizes.sm,
    marginRight: spacing[3],
  },
  clearFilters: {
    color: colors.accent,
    fontSize: typography.sizes.sm,
    fontWeight: typography.weights.bold,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  advancedToggle: {
    marginTop: spacing[3],
    paddingVertical: spacing[2],
    alignItems: 'center',
  },
  advancedToggleText: {
    fontSize: typography.sizes.sm,
    fontWeight: typography.weights.bold,
    color: colors.accent,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  advancedPanel: {
    marginTop: spacing[3],
    padding: spacing[3],
    backgroundColor: colors.background,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
    position: 'relative',
  },
  advancedLabel: {
    fontSize: typography.sizes.xs,
    fontWeight: typography.weights.bold,
    color: colors.textMuted,
    textTransform: 'uppercase',
    marginTop: spacing[3],
    marginBottom: spacing[2],
  },
  dateRow: {
    flexDirection: 'row',
    gap: spacing[2],
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
    backgroundColor: 'rgba(248,250,252,0.94)',
    borderRadius: radii.lg,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
    padding: spacing[4],
  },
  premiumOverlayText: {
    fontSize: typography.sizes.md,
    fontWeight: typography.weights.bold,
    color: colors.text,
    textAlign: 'center',
    marginBottom: spacing[3],
  },
  premiumOverlayButton: {
    backgroundColor: colors.accent,
    borderRadius: radii.md,
    paddingVertical: spacing[3],
    paddingHorizontal: spacing[4],
    overflow: 'hidden',
  },
  premiumOverlayButtonText: {
    color: colors.textInverse,
    fontSize: typography.sizes.base,
    fontWeight: typography.weights.bold,
  },
  errorBanner: {
    marginHorizontal: spacing[4],
    marginTop: spacing[3],
    padding: spacing[3],
    borderRadius: radii.lg,
    backgroundColor: palette.red[50],
    borderWidth: 1,
    borderColor: palette.red[200],
  },
  errorBannerText: {
    color: colors.error,
    fontSize: typography.sizes.base,
    fontWeight: typography.weights.semibold,
  },
  footerLoader: {
    marginVertical: spacing[4],
  },
});

const postStyles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing[3],
  },
  date: {
    fontSize: typography.sizes.sm,
    color: colors.textMuted,
  },
  title: {
    fontSize: typography.sizes.lg,
    fontWeight: typography.weights.bold,
    color: colors.text,
    marginBottom: spacing[2],
    lineHeight: typography.lineHeights.relaxed,
  },
  summary: {
    fontSize: typography.sizes.base,
    color: colors.textMuted,
    lineHeight: typography.lineHeights.normal,
    marginBottom: spacing[3],
  },
  footer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing[3],
  },
  county: {
    fontSize: typography.sizes.sm,
    fontWeight: typography.weights.bold,
    color: colors.accent,
  },
  agency: {
    fontSize: typography.sizes.sm,
    color: colors.textMuted,
  },
});
