import React from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
} from 'react-native';
import { LawCategory } from '../types';
import { COLORS } from '../constants';

interface RouteParams {
  category: LawCategory;
}

export default function LawsDetailScreen({ route }: { route: { params: RouteParams } }) {
  const { category } = route.params;

  return (
    <ScrollView style={styles.container}>
      <View style={[styles.header, { backgroundColor: category.color }]}>
        <Text style={styles.headerIcon}>{category.icon}</Text>
        <Text style={styles.headerTitle}>{category.title}</Text>
        <Text style={styles.headerSubtitle}>{category.subtitle}</Text>
      </View>

      <View style={styles.content}>
        {category.laws.map(law => (
          <View key={law.id} style={styles.card}>
            <Text style={styles.lawTitle}>{law.title}</Text>
            <Text style={styles.lawDescription}>{law.description}</Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    padding: 20,
    paddingTop: 20,
  },
  headerIcon: {
    fontSize: 32,
    marginBottom: 8,
  },
  headerTitle: {
    fontSize: 22,
    fontWeight: '700',
    color: COLORS.card,
    marginBottom: 4,
  },
  headerSubtitle: {
    fontSize: 13,
    color: 'rgba(255,255,255,0.8)',
  },
  content: {
    padding: 16,
  },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  lawTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: COLORS.primary,
    marginBottom: 8,
  },
  lawDescription: {
    fontSize: 14,
    color: COLORS.secondary,
    lineHeight: 22,
  },
});
