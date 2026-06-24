import React from 'react';
import { View, Text, StyleSheet, ViewStyle, StyleProp } from 'react-native';
import { colors, spacing, typography, radii } from '../../theme';

interface ScreenHeaderProps {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  variant?: 'dark' | 'light';
}

export default function ScreenHeader({
  eyebrow,
  title,
  subtitle,
  children,
  style,
  variant = 'dark',
}: ScreenHeaderProps) {
  const isDark = variant === 'dark';

  return (
    <View style={[styles.header, { backgroundColor: isDark ? colors.primary : colors.card }, style]}>
      {eyebrow ? (
        <Text style={[styles.eyebrow, { color: isDark ? paletteOrange300 : colors.accent }]}>
          {eyebrow}
        </Text>
      ) : null}
      <Text style={[styles.title, { color: isDark ? colors.textInverse : colors.text }]}>
        {title}
      </Text>
      {subtitle ? (
        <Text style={[styles.subtitle, { color: isDark ? '#94a3b8' : colors.textMuted }]}>
          {subtitle}
        </Text>
      ) : null}
      {children ? <View style={styles.children}>{children}</View> : null}
    </View>
  );
}

const paletteOrange300 = '#fdba74';

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: spacing[5],
    paddingTop: spacing[6],
    paddingBottom: spacing[5],
  },
  eyebrow: {
    fontSize: typography.sizes.sm,
    fontWeight: typography.weights.bold,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: spacing[2],
  },
  title: {
    fontSize: typography.sizes['4xl'],
    fontWeight: typography.weights.extrabold,
    marginBottom: spacing[2],
  },
  subtitle: {
    fontSize: typography.sizes.base,
    lineHeight: typography.lineHeights.normal,
  },
  children: {
    marginTop: spacing[4],
  },
});
