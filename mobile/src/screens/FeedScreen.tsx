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
} from 'react-native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useNavigation } from '@react-navigation/native';
import { api } from '../services/api';
import { Post } from '../types';
import { COLORS } from '../constants';

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
};

export default function FeedScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<FeedStackParamList>>();
  const [posts, setPosts] = useState<Post[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [search, setSearch] = useState('');

  const fetchPosts = useCallback(async (pageNum: number = 1, searchQuery: string = '') => {
    try {
      const data = await api.getPosts({ page: pageNum, per_page: 20, search: searchQuery });
      if (pageNum === 1) {
        setPosts(data.posts);
      } else {
        setPosts(prev => [...prev, ...data.posts]);
      }
      setTotalPages(data.total_pages);
    } catch (error) {
      console.error('Failed to fetch posts:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchPosts(1, search);
  }, [search]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    setPage(1);
    fetchPosts(1, search);
  }, [search, fetchPosts]);

  const onEndReached = useCallback(() => {
    if (page < totalPages && !loading) {
      const nextPage = page + 1;
      setPage(nextPage);
      fetchPosts(nextPage, search);
    }
  }, [page, totalPages, loading, search, fetchPosts]);

  const renderPost = ({ item }: { item: Post }) => (
    <TouchableOpacity
      style={styles.card}
      onPress={() => navigation.navigate('PostDetail', { postId: item.id })}
    >
      <View style={styles.postHeader}>
        <Text style={styles.agencyType}>
          {item.agency_type === 'Sheriff' ? '👮' : '🚔'} {item.agency_type}
        </Text>
        <Text style={styles.date}>{item.incident_date}</Text>
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
        <Text style={styles.loadingText}>Loading blotter...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.searchContainer}>
        <TextInput
          style={styles.searchInput}
          placeholder="Search incidents..."
          value={search}
          onChangeText={setSearch}
          placeholderTextColor={COLORS.secondary}
        />
      </View>
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
  },
});
