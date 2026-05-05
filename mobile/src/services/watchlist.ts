import AsyncStorage from '@react-native-async-storage/async-storage';
import { Post } from '../types';

const WATCHLIST_KEY = '@mb_watchlist';

export async function getWatchlist(): Promise<Post[]> {
  const raw = await AsyncStorage.getItem(WATCHLIST_KEY);
  return raw ? JSON.parse(raw) : [];
}

export async function addToWatchlist(post: Post) {
  const list = await getWatchlist();
  if (list.find((p) => p.id === post.id)) {
    return;
  }
  const next = [post, ...list];
  await AsyncStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
}

export async function removeFromWatchlist(postId: number) {
  const list = await getWatchlist();
  const next = list.filter((p) => p.id !== postId);
  await AsyncStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
}

export async function isInWatchlist(postId: number): Promise<boolean> {
  const list = await getWatchlist();
  return list.some((p) => p.id === postId);
}
