import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView, ActivityIndicator, StyleSheet, Pressable, Alert, Share } from 'react-native';
import { api } from '../services/api';
import { Post } from '../types';
import { API_BASE_URL } from '../constants';
import { usePremium } from '../context/PremiumContext';
import { addToWatchlist, removeFromWatchlist, isInWatchlist } from '../services/watchlist';
import { Badge, Button, Card, LoadingState } from '../components/ui';
import { colors, radii, spacing, typography } from '../theme';

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

  const toggleSave = async () => {
    if (!isPremium) {
      Alert.alert('Premium Feature', 'Save to Watchlist is available with Premium.');
      return;
    }
    if (!post) return;
    if (saved) {
      await removeFromWatchlist(post.id);
      setSaved(false);
    } else {
      await addToWatchlist(post);
      setSaved(true);
    }
  };

  const handleShare = async () => {
    if (!post) return;
    await Share.share({
      message: `${post.title}\n\n${post.summary}\n\n${API_BASE_URL}/post/${post.id}`,
      title: post.title,
    });
  };

  if (loading) {
    return <LoadingState message="Loading post..." />;
  }

  if (!post) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.errorText}>Post not found</Text>
      </View>
    );
  }

  const formatDate = (value?: string) => {
    if (!value) return 'Unknown';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
  };

  const metaItems = [
    { label: 'County', value: post.county },
    { label: 'Agency', value: post.agency_name },
    ...(post.incident_type ? [{ label: 'Incident Type', value: post.incident_type }] : []),
    { label: 'Reported', value: formatDate(post.created_at) },
  ];

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <Badge label={post.agency_type || 'Agency'} variant="default" />
          <Pressable
            onPress={toggleSave}
            android_ripple={{ color: 'rgba(0,0,0,0.06)', borderless: true }}
            style={styles.saveButton}
            hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
          >
            <Text style={styles.saveButtonText}>{saved ? '★ Saved' : '☆ Save'}</Text>
            {!isPremium && <Text style={styles.premiumLabel}>Premium</Text>}
          </Pressable>
        </View>
        <Text style={styles.date}>{formatDate(post.incident_date)}</Text>
        <Text style={styles.title}>{post.title}</Text>
      </View>

      <View style={styles.metaGrid}>
        {metaItems.map((item) => (
          <Card key={item.label} style={styles.metaCard} padded shadow="none">
            <Text style={styles.metaLabel}>{item.label}</Text>
            <Text style={styles.metaValue} numberOfLines={2}>
              {item.value}
            </Text>
          </Card>
        ))}
      </View>

      <Card style={styles.summaryCard}>
        <Text style={styles.sectionTitle}>Summary</Text>
        <Text style={styles.summary}>{post.summary}</Text>
      </Card>

      {post.source_pdf_name && (
        <Card style={styles.pdfCard}>
          <Text style={styles.sectionTitle}>Source Document</Text>
          <Text style={styles.pdfName}>{post.source_pdf_name}</Text>
          <Button
            title="Open original PDF"
            onPress={() => {
              const url = `${API_BASE_URL}/uploads/${encodeURIComponent(post.source_pdf_name!)}`;
              // On Android, Linking is used; on iOS, Share may work better for PDFs.
              Share.share({ url, title: post.title }).catch(() => {
                Alert.alert('Unable to open PDF', 'No application can handle this file.');
              });
            }}
            variant="primary"
            size="md"
          />
        </Card>
      )}

      {isPremium && (
        <Card style={styles.exportCard}>
          <Text style={styles.sectionTitle}>Share</Text>
          <Button title="Share this post" onPress={handleShare} variant="outline" size="md" />
        </Card>
      )}

      {!isPremium && (
        <Card style={styles.teaserCard}>
          <Text style={styles.teaserText}>📤 Save posts and share with Premium</Text>
          <Button title="Upgrade to Premium" onPress={() => {}} variant="primary" size="sm" />
        </Card>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    paddingBottom: spacing[8],
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.background,
  },
  errorText: {
    fontSize: typography.sizes.lg,
    color: colors.error,
  },
  header: {
    backgroundColor: colors.primary,
    paddingHorizontal: spacing[5],
    paddingTop: spacing[6],
    paddingBottom: spacing[5],
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing[3],
  },
  date: {
    fontSize: typography.sizes.sm,
    color: '#94a3b8',
    marginBottom: spacing[3],
  },
  title: {
    fontSize: typography.sizes['3xl'],
    fontWeight: typography.weights.extrabold,
    color: colors.textInverse,
    lineHeight: typography.lineHeights.loose,
  },
  saveButton: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.glassLight,
    paddingHorizontal: spacing[3],
    paddingVertical: spacing[2],
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: colors.glassBorder,
  },
  saveButtonText: {
    fontSize: typography.sizes.sm,
    fontWeight: typography.weights.bold,
    color: colors.accent,
  },
  premiumLabel: {
    fontSize: typography.sizes.xs,
    fontWeight: typography.weights.bold,
    color: '#cbd5e1',
    textTransform: 'uppercase',
    marginLeft: spacing[2],
  },
  metaGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    padding: spacing[4],
    gap: spacing[3],
  },
  metaCard: {
    flex: 1,
    minWidth: 140,
  },
  metaLabel: {
    fontSize: typography.sizes.xs,
    color: colors.textMuted,
    textTransform: 'uppercase',
    fontWeight: typography.weights.bold,
    letterSpacing: 0.5,
    marginBottom: spacing[1],
  },
  metaValue: {
    fontSize: typography.sizes.md,
    fontWeight: typography.weights.semibold,
    color: colors.text,
  },
  summaryCard: {
    marginHorizontal: spacing[4],
    marginBottom: spacing[4],
  },
  sectionTitle: {
    fontSize: typography.sizes.xl,
    fontWeight: typography.weights.bold,
    color: colors.text,
    marginBottom: spacing[3],
  },
  summary: {
    fontSize: typography.sizes.md,
    color: colors.textMuted,
    lineHeight: typography.lineHeights.relaxed,
  },
  pdfCard: {
    marginHorizontal: spacing[4],
    marginBottom: spacing[4],
  },
  pdfName: {
    fontSize: typography.sizes.sm,
    color: colors.textMuted,
    marginBottom: spacing[3],
  },
  exportCard: {
    marginHorizontal: spacing[4],
    marginBottom: spacing[4],
  },
  teaserCard: {
    marginHorizontal: spacing[4],
    alignItems: 'center',
    gap: spacing[3],
  },
  teaserText: {
    fontSize: typography.sizes.base,
    color: colors.textMuted,
    fontWeight: typography.weights.semibold,
  },
});
