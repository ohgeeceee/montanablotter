import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { COLORS } from '../constants';

type MoreStackParamList = {
  MoreHome: undefined;
  Warrants: undefined;
  Court: undefined;
  People: undefined;
  Diagnostics: undefined;
};

const MENU_ITEMS: {
  key: keyof MoreStackParamList;
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
}[] = [
  {
    key: 'Warrants',
    title: 'Active Warrants',
    subtitle: 'Search warrants by county or name',
    icon: 'document-text',
  },
  {
    key: 'Court',
    title: 'Court Lookup',
    subtitle: 'Criminal case status and outcomes',
    icon: 'scale',
  },
  {
    key: 'People',
    title: 'Missing Persons',
    subtitle: 'Active alerts and located persons',
    icon: 'people',
  },
  {
    key: 'Diagnostics',
    title: 'Diagnostics',
    subtitle: 'App health and debugging tools',
    icon: 'construct',
  },
];

export default function MoreScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<MoreStackParamList>>();

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerEyebrow}>More Tools</Text>
        <Text style={styles.headerTitle}>Explore</Text>
        <Text style={styles.headerSubtitle}>
          Additional records and tools
        </Text>
      </View>
      <ScrollView contentContainerStyle={styles.list}>
        {MENU_ITEMS.map((item) => (
          <TouchableOpacity
            key={item.key}
            style={styles.card}
            onPress={() => navigation.navigate(item.key)}
          >
            <View style={styles.iconBox}>
              <Ionicons name={item.icon} size={24} color={COLORS.accent} />
            </View>
            <View style={styles.textBox}>
              <Text style={styles.title}>{item.title}</Text>
              <Text style={styles.subtitle}>{item.subtitle}</Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={COLORS.secondary} />
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  header: {
    backgroundColor: COLORS.primary,
    padding: 20,
    paddingTop: 24,
  },
  headerEyebrow: {
    fontSize: 12,
    fontWeight: '700',
    color: '#fdba74',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: '700',
    color: COLORS.card,
    marginBottom: 8,
  },
  headerSubtitle: {
    fontSize: 14,
    color: '#94a3b8',
    lineHeight: 20,
  },
  list: {
    padding: 16,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  iconBox: {
    width: 44,
    height: 44,
    borderRadius: 10,
    backgroundColor: '#fff7ed',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  textBox: {
    flex: 1,
  },
  title: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    marginBottom: 2,
  },
  subtitle: {
    fontSize: 12,
    color: COLORS.secondary,
  },
});
