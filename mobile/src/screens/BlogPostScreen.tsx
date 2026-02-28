import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
} from 'react-native';
import { blogApi } from '../services/api';
import { BlogPost } from '../types';
import { COLORS } from '../constants';

interface RouteParams {
  slug: string;
}

export default function BlogPostScreen({ route }: { route: { params: RouteParams } }) {
  const { slug } = route.params;
  const [post, setPost] = useState<BlogPost | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchPost = async () => {
      try {
        const data = await blogApi.getPost(slug);
        setPost(data);
      } catch (error) {
        console.error('Failed to fetch blog post:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchPost();
  }, [slug]);

  if (loading) {
    return (
      <View style={styles.centerContainer}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }

  if (!post) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.errorText}>Post not found</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <View style={styles.metaContainer}>
          <Text style={styles.date}>
            {post.created_at ? post.created_at.substring(0, 10) : ''}
          </Text>
          {post.author && (
            <Text style={styles.author}>· By {post.author}</Text>
          )}
        </View>
        <Text style={styles.title}>{post.title}</Text>
      </View>

      <View style={styles.content}>
        <Text style={styles.body}>{post.body}</Text>
      </View>
    </ScrollView>
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
  errorText: {
    fontSize: 16,
    color: COLORS.error,
  },
  header: {
    backgroundColor: COLORS.card,
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  metaContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  date: {
    fontSize: 13,
    color: COLORS.secondary,
  },
  author: {
    fontSize: 13,
    color: COLORS.secondary,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: COLORS.primary,
    lineHeight: 32,
  },
  content: {
    padding: 20,
  },
  body: {
    fontSize: 16,
    color: COLORS.primary,
    lineHeight: 26,
  },
});
