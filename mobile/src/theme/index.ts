/**
 * Montana Blotter design system.
 *
 * Single source of truth for colors, typography, spacing, shadows, and radii.
 * All new UI should pull from these tokens instead of hard-coding values.
 */

export const palette = {
  slate: {
    50: '#f8fafc',
    100: '#f1f5f9',
    200: '#e2e8f0',
    300: '#cbd5e1',
    400: '#94a3b8',
    500: '#64748b',
    600: '#475569',
    700: '#334155',
    800: '#1e293b',
    900: '#0f172a',
    950: '#020617',
  },
  orange: {
    50: '#fff7ed',
    100: '#ffedd5',
    200: '#fed7aa',
    300: '#fdba74',
    400: '#fb923c',
    500: '#f97316',
    600: '#ea580c',
    700: '#c2410c',
    800: '#9a3412',
    900: '#7c2d12',
    950: '#431407',
  },
  emerald: {
    50: '#ecfdf5',
    100: '#d1fae5',
    200: '#a7f3d0',
    300: '#6ee7b7',
    400: '#34d399',
    500: '#10b981',
    600: '#059669',
    700: '#047857',
  },
  red: {
    50: '#fef2f2',
    100: '#fee2e2',
    200: '#fecaca',
    300: '#fca5a5',
    400: '#f87171',
    500: '#ef4444',
    600: '#dc2626',
  },
  blue: {
    50: '#eff6ff',
    100: '#dbeafe',
    500: '#3b82f6',
    600: '#2563eb',
    700: '#1d4ed8',
  },
} as const;

export const colors = {
  // Brand
  primary: palette.slate[900],
  primaryForeground: '#ffffff',
  accent: palette.orange[500],
  accentForeground: '#ffffff',
  accentSoft: palette.orange[50],

  // Semantic
  background: palette.slate[50],
  card: '#ffffff',
  border: palette.slate[200],
  divider: palette.slate[200],

  text: palette.slate[900],
  textMuted: palette.slate[500],
  textInverse: '#ffffff',

  success: palette.emerald[500],
  error: palette.red[500],
  warning: '#f59e0b',
  info: palette.blue[600],

  // Overlays
  glassLight: 'rgba(255,255,255,0.08)',
  glassBorder: 'rgba(255,255,255,0.12)',
} as const;

export const typography = {
  sizes: {
    xs: 11,
    sm: 12,
    base: 14,
    md: 15,
    lg: 16,
    xl: 18,
    '2xl': 20,
    '3xl': 22,
    '4xl': 26,
  },
  weights: {
    normal: '400' as const,
    medium: '500' as const,
    semibold: '600' as const,
    bold: '700' as const,
    extrabold: '800' as const,
  },
  lineHeights: {
    tight: 20,
    normal: 22,
    relaxed: 24,
    loose: 28,
  },
} as const;

export const spacing = {
  0: 0,
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  8: 32,
  10: 40,
} as const;

export const radii = {
  sm: 6,
  md: 8,
  lg: 12,
  xl: 16,
  '2xl': 20,
  full: 999,
} as const;

export const shadows = {
  sm: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 2,
  },
  md: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 4,
    elevation: 4,
  },
} as const;

export const hitSlop = {
  icon: { top: 8, right: 8, bottom: 8, left: 8 },
  button: { top: 6, right: 6, bottom: 6, left: 6 },
} as const;
