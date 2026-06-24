import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { colors, palette, radii, spacing, typography } from '../../theme';

type BadgeVariant = 'default' | 'success' | 'warning' | 'info' | 'muted';

interface BadgeProps {
  label: string;
  variant?: BadgeVariant;
}

const variantStyles: Record<BadgeVariant, { bg: string; text: string; border: string }> = {
  default: { bg: colors.primary, text: colors.primaryForeground, border: colors.primary },
  success: { bg: palette.emerald[50], text: palette.emerald[700], border: palette.emerald[200] },
  warning: { bg: '#fffbeb', text: '#92400e', border: '#fde68a' },
  info: { bg: palette.blue[50], text: palette.blue[700], border: palette.blue[100] },
  muted: { bg: palette.slate[100], text: colors.textMuted, border: palette.slate[200] },
};

export default function Badge({ label, variant = 'default' }: BadgeProps) {
  const style = variantStyles[variant];
  return (
    <View style={[styles.badge, { backgroundColor: style.bg, borderColor: style.border }]}>
      <Text style={[styles.text, { color: style.text }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: spacing[3],
    paddingVertical: spacing[1],
    borderRadius: radii.full,
    borderWidth: 1,
  },
  text: {
    fontSize: typography.sizes.xs,
    fontWeight: typography.weights.bold,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
});
