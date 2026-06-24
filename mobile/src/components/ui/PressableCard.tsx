import React from 'react';
import { Pressable, StyleSheet, ViewStyle, StyleProp } from 'react-native';
import { colors, radii, shadows, spacing } from '../../theme';

interface PressableCardProps {
  children: React.ReactNode;
  onPress: () => void;
  style?: StyleProp<ViewStyle>;
  shadow?: 'sm' | 'md' | 'none';
  disabled?: boolean;
}

export default function PressableCard({
  children,
  onPress,
  style,
  shadow = 'sm',
  disabled = false,
}: PressableCardProps) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      android_ripple={{ color: 'rgba(0,0,0,0.04)', foreground: true }}
      style={({ pressed }) => [
        styles.base,
        shadow === 'sm' && shadows.sm,
        shadow === 'md' && shadows.md,
        pressed && styles.pressed,
        style,
      ]}
    >
      {children}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    backgroundColor: colors.card,
    borderRadius: radii.lg,
    padding: spacing[4],
    marginBottom: spacing[3],
    borderWidth: 1,
    borderColor: colors.border,
    overflow: 'hidden',
  },
  pressed: {
    opacity: 0.96,
  },
});
