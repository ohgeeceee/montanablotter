import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  ScrollView,
  ActivityIndicator,
  StyleSheet,
  TouchableOpacity,
  Linking,
  Alert,
} from 'react-native';
import { api } from '../services/api';
import { Post } from '../types';
import { API_BASE_URL, COLORS } from '../constants';
import { usePremium } from '../context/PremiumContext';
import { addToWatchlist, removeFromWatchlist, isInWatchlist } from '../services/watchlist';
import { Share } from 'react-native';

interface RouteParams {
  postId: number;
}

export default function PostDetailScreen({ route }: { route: { params: RouteParams } }) {
  const { postId } = route.params;
  const [post, setPost] = useState<Post | null>(null);
  const [loading, setLoading] = useState(true);
  const [saved, setSaved] = useState(false);
  const { isPremium } = usePremium();

  useEffect(() => {
    const fetchPost = async () => {
      try {
        const data = await api.getPost(postId);
        setPost(data);
        const inList = await isInWatchlist(postId);
        setSaved(inList);
      } catch (error) {
        console.error('Failed to fetch post:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchPost();
  }, [postId]);

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
        <View style={styles.badge}>
          <Text style={styles.badgeText}>
            {post.agency_type === 'Sheriff' ? '👮' : '🚔'} {post.agency_type}
          </Text>
        </View>
        <Text style={styles.date}>{post.incident_date}</Text>
        <TouchableOpacity
          style={styles.saveButton}
          onPress={async () => {
            if (!isPremium) {
              Alert.alert('Premium Feature', 'Save to Watchlist is available with Premium.');
              return;
            }
            if (saved) {
              await removeFromWatchlist(post.id);
              setSaved(false);
            } else {
              await addToWatchlist(post);
              setSaved(true);
            }
          }}
        >
          <Text style={styles.saveButtonText}>{saved ? '★ Saved' : '☆ Save'}</Text>
          {!isPremium && <Text style={styles.premiumLabel}>Premium</Text>}
        </TouchableOpacity>
      </View>

      <Text style={styles.title}>{post.title}</Text>

      <View style={styles.metaContainer}>
        <View style={styles.metaItem}>
          <Text style={styles.metaLabel}>County</Text>
          <Text style={styles.metaValue}>{post.county}</Text>
        </View>
        <View style={styles.metaItem}>
          <Text style={styles.metaLabel}>Agency</Text>
          <Text style={styles.metaValue}>{post.agency_name}</Text>
        </View>
        {post.incident_type && (
          <View style={styles.metaItem}>
            <Text style={styles.metaLabel}>Type</Text>
            <Text style={styles.metaValue}>{post.incident_type}</Text>
          </View>
        )}
      </View>

      <View style={styles.summaryContainer}>
        <Text style={styles.summaryTitle}>Summary</Text>
        <Text style={styles.summary}>{post.summary}</Text>
      </View>

      {post.source_pdf_name ? (
        <View style={styles.pdfSection}>
          <Text style={styles.summaryTitle}>Source PDF</Text>
          <TouchableOpacity
            style={styles.pdfButton}
            onPress={() => {
              Linking.openURL(`${API_BASE_URL}/uploads/${encodeURIComponent(post.source_pdf_name!)}`)
                .catch((error) => console.error('Failed to open source PDF:', error));
            }}
          >
            <Text style={styles.pdfButtonText}>Open original PDF</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      <View style={styles.footer}>
        <Text style={styles.footerText}>
          Reported: {new Date(post.created_at).toLocaleDateString()}
        </Text>
      </View>

      {isPremium && post && (
        <View style={styles.exportSection}>
          <Text style={styles.summaryTitle}>Export</Text>
          <TouchableOpacity
            style={styles.exportButton}
            onPress={async () => {
              await Share.share({
                message: JSON.stringify(post, null, 2),
                title: post.title,
              });
            }}
          >
            <Text style={styles.exportButtonText}>Share as JSON</Text>
          </TouchableOpacity>
        </View>
      )}

      {!isPremium && (
        <View style={styles.exportTeaser}>
          <Text style={styles.exportTeaserText}>📤 Export available with Premium</Text>
        </View>
      )}
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
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    backgroundColor: COLORS.card,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  badge: {
    backgroundColor: COLORS.primary,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
  },
  badgeText: {
    color: COLORS.card,
    fontSize: 12,
    fontWeight: '600',
  },
  date: {
    fontSize: 14,
    color: COLORS.secondary,
  },
  title: {
    fontSize: 22,
    fontWeight: '700',
    color: COLORS.primary,
    padding: 16,
    lineHeight: 28,
  },
  metaContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    padding: 16,
    gap: 12,
  },
  metaItem: {
    backgroundColor: COLORS.card,
    padding: 12,
    borderRadius: 8,
    minWidth: 100,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  metaLabel: {
    fontSize: 11,
    color: COLORS.secondary,
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  metaValue: {
    fontSize: 14,
    fontWeight: '600',
    color: COLORS.primary,
  },
  summaryContainer: {
    padding: 16,
  },
  summaryTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: COLORS.primary,
    marginBottom: 12,
  },
  summary: {
    fontSize: 15,
    color: COLORS.secondary,
    lineHeight: 24,
  },
  pdfSection: {
    paddingHorizontal: 16,
    paddingBottom: 4,
  },
  pdfButton: {
    backgroundColor: COLORS.primary,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: 'center',
    marginTop: 8,
  },
  pdfButtonText: {
    color: COLORS.card,
    fontSize: 14,
    fontWeight: '700',
  },
  footer: {
    padding: 16,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    marginTop: 16,
  },
  footerText: {
    fontSize: 12,
    color: COLORS.secondary,
    textAlign: 'center',
  },
  saveButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: COLORS.background,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  saveButtonText: {
    fontSize: 13,
    fontWeight: '700',
    color: COLORS.accent,
  },
  premiumLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: COLORS.secondary,
    textTransform: 'uppercase',
    marginLeft: 4,
  },
  exportSection: {
    padding: 16,
  },
  exportButton: {
    backgroundColor: COLORS.primary,
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: 'center',
    marginTop: 8,
  },
  exportButtonText: {
    color: COLORS.card,
    fontSize: 14,
    fontWeight: '700',
  },
  exportTeaser: {
    padding: 16,
    alignItems: 'center',
  },
  exportTeaserText: {
    fontSize: 13,
    color: COLORS.secondary,
    fontWeight: '600',
  },
});
