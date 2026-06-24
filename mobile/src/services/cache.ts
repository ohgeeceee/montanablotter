import AsyncStorage from '@react-native-async-storage/async-storage';

const CACHE_PREFIX = '@mb_cache:';
const DEFAULT_TTL_MS = 5 * 60 * 1000; // 5 minutes

interface CacheEntry<T> {
  data: T;
  cachedAt: number;
}

function cacheKey(endpoint: string, params?: Record<string, unknown>): string {
  const paramsKey = params ? JSON.stringify(params) : '';
  return `${CACHE_PREFIX}${endpoint}:${paramsKey}`;
}

export async function getCached<T>(
  endpoint: string,
  params?: Record<string, unknown>,
  ttlMs: number = DEFAULT_TTL_MS,
): Promise<T | null> {
  try {
    const raw = await AsyncStorage.getItem(cacheKey(endpoint, params));
    if (!raw) return null;
    const entry: CacheEntry<T> = JSON.parse(raw);
    if (Date.now() - entry.cachedAt > ttlMs) {
      await AsyncStorage.removeItem(cacheKey(endpoint, params));
      return null;
    }
    return entry.data;
  } catch (error) {
    console.warn('[Cache] Read failed:', error);
    return null;
  }
}

export async function setCached<T>(
  endpoint: string,
  data: T,
  params?: Record<string, unknown>,
): Promise<void> {
  try {
    const entry: CacheEntry<T> = { data, cachedAt: Date.now() };
    await AsyncStorage.setItem(cacheKey(endpoint, params), JSON.stringify(entry));
  } catch (error) {
    console.warn('[Cache] Write failed:', error);
  }
}

export async function clearCache(): Promise<void> {
  try {
    const keys = await AsyncStorage.getAllKeys();
    const cacheKeys = keys.filter((key) => key.startsWith(CACHE_PREFIX));
    await AsyncStorage.multiRemove(cacheKeys);
  } catch (error) {
    console.warn('[Cache] Clear failed:', error);
  }
}
