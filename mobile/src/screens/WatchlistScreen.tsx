import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  RefreshControl,
} from 'react-native';
import { useNavigation, useFocusEffect } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { getWatchlist, removeFromWatchlist } from '../services/watchlist';
import { Post } from '../types';
import { COLORS } from '../constants';

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
  Watchlist: undefined;
};

export default function WatchlistScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<FeedStackParamList>>();
  const [posts, setPosts] = useState<Post[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const list = await getWatchlist();
    setPosts(list);
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const handleRemove = async (postId: number) => {
    await removeFromWatchlist(postId);
    await load();
  };

  const renderItem = ({ item }: { item: Post }) => (
    <TouchableOpacity
      style={styles.card}
      onPress={() => navigation.navigate('PostDetail', { postId: item.id })}
    >
      <View style={styles.row}>
        <Text style={styles.title} numberOfLines={2}>{item.title}</Text>
        <TouchableOpacity onPress={() => handleRemove(item.id)}>
          <Text style={styles.remove}>✕</Text>
        </TouchableOpacity>
      </View>
      <Text style={styles.meta}>{item.county} County · {item.agency_name}</Text>
    </TouchableOpacity>
  );

  return (
    <View style={styles.container}>
      <FlatList
        data={posts}
        keyExtractor={(item) => String(item.id)}
        renderItem={renderItem}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No saved posts yet.</Text>
            <Text style={styles.emptySubtext}>Premium users can save posts to their watchlist.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  list: { padding: 16 },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  title: { flex: 1, fontSize: 15, fontWeight: '700', color: COLORS.primary, marginRight: 8 },
  remove: { fontSize: 16, color: COLORS.error, fontWeight: '700' },
  meta: { fontSize: 12, color: COLORS.secondary, marginTop: 6 },
  empty: { padding: 40, alignItems: 'center' },
  emptyText: { fontSize: 16, fontWeight: '700', color: COLORS.primary, marginBottom: 4 },
  emptySubtext: { fontSize: 14, color: COLORS.secondary, textAlign: 'center' },
});
