const rawApiBaseUrl = process.env.EXPO_PUBLIC_API_BASE_URL;
export const API_BASE_URL = (rawApiBaseUrl && rawApiBaseUrl.trim()) || 'https://montanablotter.com';

export const COLORS = {
  primary: '#0f172a',
  secondary: '#64748b',
  accent: '#f97316',
  background: '#f8fafc',
  card: '#ffffff',
  border: '#e2e8f0',
  success: '#22c55e',
  error: '#ef4444',
  warning: '#f59e0b',
  info: '#3b82f6',
};

export const FONTS = {
  regular: 'System',
  medium: 'System',
  bold: 'System',
};
