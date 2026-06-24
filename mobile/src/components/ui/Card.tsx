import React from 'react';
import { View, StyleSheet, ViewStyle, StyleProp } from 'react-native';
import { colors, radii, shadows, spacing } from '../../theme';

interface CardProps {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  padded?: boolean;
  shadow?: 'sm' | 'md' | 'none';
}

export default function Card({ children, style, padded = true, shadow = 'sm' }: CardProps) {
  return (
    <View
      style={[
        styles.base,
        padded && styles.padded,
        shadow === 'sm' && shadows.sm,
        shadow === 'md' && shadows.md,
        style,
      ]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    backgroundColor: colors.card,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  padded: {
    padding: spacing[4],
  },
});
