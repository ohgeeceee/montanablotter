import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
  StyleSheet,
} from 'react-native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useNavigation } from '@react-navigation/native';
import { blogApi } from '../services/api';
import { BlogPost } from '../types';
import { COLORS } from '../constants';

type BlogStackParamList = {
  BlogHome: undefined;
  BlogPost: { slug: string };
};

export default function BlogScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<BlogStackParamList>>();
  const [posts, setPosts] = useState<BlogPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  const fetchPosts = useCallback(async (pageNum: number = 1) => {
    try {
      const data = await blogApi.getPosts(pageNum);
      if (pageNum === 1) {
        setPosts(data.posts);
      } else {
        setPosts(prev => [...prev, ...data.posts]);
      }
      setTotalPages(data.total_pages);
    } catch (error) {
      console.error('Failed to fetch blog posts:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchPosts(1);
  }, []);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    setPage(1);
    fetchPosts(1);
  }, [fetchPosts]);

  const onEndReached = useCallback(() => {
    if (page < totalPages && !loading) {
      const nextPage = page + 1;
      setPage(nextPage);
      fetchPosts(nextPage);
    }
  }, [page, totalPages, loading, fetchPosts]);

  const renderPost = ({ item }: { item: BlogPost }) => (
    <TouchableOpacity
      style={styles.card}
      onPress={() => navigation.navigate('BlogPost', { slug: item.slug })}
    >
      <View style={styles.postHeader}>
        <Text style={styles.date}>
          {item.created_at ? item.created_at.substring(0, 10) : ''}
        </Text>
        {item.author && <Text style={styles.author}>· {item.author}</Text>}
      </View>
      <Text style={styles.title} numberOfLines={2}>
        {item.title}
      </Text>
      {item.excerpt && (
        <Text style={styles.excerpt} numberOfLines={3}>
          {item.excerpt}
        </Text>
      )}
      <Text style={styles.readMore}>Read more →</Text>
    </TouchableOpacity>
  );

  if (loading && posts.length === 0) {
    return (
      <View style={styles.centerContainer}>
        <ActivityIndicator size="large" color={COLORS.primary} />
        <Text style={styles.loadingText}>Loading blog...</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>News & Analysis</Text>
        <Text style={styles.headerSubtitle}>
          Insights on Montana public safety and community reporting
        </Text>
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
            <Text style={styles.emptyIcon}>📝</Text>
            <Text style={styles.emptyText}>No posts yet</Text>
            <Text style={styles.emptySubtext}>Check back soon for updates</Text>
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
  header: {
    backgroundColor: COLORS.primary,
    padding: 20,
    paddingTop: 60,
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
  postHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  date: {
    fontSize: 12,
    color: COLORS.secondary,
  },
  author: {
    fontSize: 12,
    color: COLORS.secondary,
  },
  title: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.primary,
    marginBottom: 8,
    lineHeight: 24,
  },
  excerpt: {
    fontSize: 14,
    color: COLORS.secondary,
    lineHeight: 20,
    marginBottom: 12,
  },
  readMore: {
    fontSize: 13,
    fontWeight: '600',
    color: '#10b981',
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    paddingTop: 50,
  },
  emptyIcon: {
    fontSize: 48,
    marginBottom: 16,
  },
  emptyText: {
    fontSize: 18,
    fontWeight: '600',
    color: COLORS.primary,
    marginBottom: 4,
  },
  emptySubtext: {
    fontSize: 14,
    color: COLORS.secondary,
  },
});
