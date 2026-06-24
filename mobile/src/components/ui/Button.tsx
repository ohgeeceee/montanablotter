import React from 'react';
import { Pressable, Text, StyleSheet, ViewStyle, TextStyle, StyleProp } from 'react-native';
import { colors, palette, radii, spacing, typography } from '../../theme';

interface ButtonProps {
  title: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}

export default function Button({
  title,
  onPress,
  variant = 'primary',
  size = 'md',
  disabled = false,
  style,
  textStyle,
}: ButtonProps) {
  const backgroundColor = {
    primary: colors.accent,
    secondary: palette.slate[100],
    outline: 'transparent',
    ghost: 'transparent',
  }[variant];

  const textColor = {
    primary: colors.accentForeground,
    secondary: colors.text,
    outline: colors.accent,
    ghost: colors.accent,
  }[variant];

  const padding = {
    sm: { paddingVertical: spacing[2], paddingHorizontal: spacing[3] },
    md: { paddingVertical: spacing[3], paddingHorizontal: spacing[4] },
    lg: { paddingVertical: spacing[4], paddingHorizontal: spacing[5] },
  }[size];

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      android_ripple={{
        color: variant === 'primary' ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.06)',
        foreground: true,
      }}
      style={({ pressed }) => [
        styles.base,
        { backgroundColor },
        padding,
        variant === 'outline' && styles.outline,
        disabled && styles.disabled,
        pressed && styles.pressed,
        style,
      ]}
    >
      <Text style={[styles.text, { color: textColor }, textStyle]}>{title}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    borderRadius: radii.md,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  outline: {
    borderWidth: 1,
    borderColor: colors.accent,
  },
  disabled: {
    opacity: 0.5,
  },
  pressed: {
    opacity: 0.9,
  },
  text: {
    fontSize: typography.sizes.base,
    fontWeight: typography.weights.bold,
  },
});
