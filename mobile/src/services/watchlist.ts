import AsyncStorage from '@react-native-async-storage/async-storage';
import { Post } from '../types';
import { getToken } from './auth';
import { watchlistApi } from './me';

const WATCHLIST_KEY = '@mb_watchlist';

export async function getWatchlist(): Promise<Post[]> {
  const token = await getToken();
  if (token) {
    try {
      const response = await watchlistApi.get();
      return response.posts;
    } catch (err) {
      console.error('[Watchlist] Failed to fetch from backend:', err);
      return [];
    }
  }
  const raw = await AsyncStorage.getItem(WATCHLIST_KEY);
  return raw ? JSON.parse(raw) : [];
}

export async function addToWatchlist(post: Post) {
  const token = await getToken();
  if (token) {
    try {
      await watchlistApi.add(post.id);
      return;
    } catch (err) {
      console.error('[Watchlist] Failed to add to backend:', err);
    }
  }
  const list = await getWatchlist();
  if (list.find((p) => p.id === post.id)) {
    return;
  }
  const next = [post, ...list];
  await AsyncStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
}

export async function removeFromWatchlist(postId: number) {
  const token = await getToken();
  if (token) {
    try {
      await watchlistApi.remove(postId);
      return;
    } catch (err) {
      console.error('[Watchlist] Failed to remove from backend:', err);
    }
  }
  const list = await getWatchlist();
  const next = list.filter((p) => p.id !== postId);
  await AsyncStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
}

export async function isInWatchlist(postId: number): Promise<boolean> {
  const list = await getWatchlist();
  return list.some((p) => p.id === postId);
}
