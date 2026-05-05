import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  FlatList,
  StyleSheet,
  Switch,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { usePremium } from '../context/PremiumContext';
import { getAlertRules, addAlertRule, removeAlertRule, toggleAlertRule, AlertRule } from '../services/alerts';
import { COLORS } from '../constants';

export default function AlertsScreen() {
  const { isPremium } = usePremium();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [county, setCounty] = useState('');
  const [incidentType, setIncidentType] = useState('');

  const load = useCallback(async () => {
    const data = await getAlertRules();
    setRules(data);
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handleAdd = async () => {
    if (!county.trim() || !incidentType.trim()) return;
    const rule: AlertRule = {
      id: `${Date.now()}`,
      county: county.trim(),
      incident_type: incidentType.trim(),
      enabled: true,
    };
    await addAlertRule(rule);
    setCounty('');
    setIncidentType('');
    await load();
  };

  const handleRemove = async (id: string) => {
    await removeAlertRule(id);
    await load();
  };

  const handleToggle = async (id: string, value: boolean) => {
    await toggleAlertRule(id, value);
    await load();
  };

  if (!isPremium) {
    return (
      <View style={styles.centerContainer}>
        <Text style={styles.lockTitle}>🔒 Alerts are Premium</Text>
        <Text style={styles.lockSubtext}>Upgrade to create custom incident alerts.</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.form}>
        <Text style={styles.label}>County</Text>
        <TextInput style={styles.input} value={county} onChangeText={setCounty} placeholder="e.g. Gallatin" />
        <Text style={styles.label}>Incident Type</Text>
        <TextInput style={styles.input} value={incidentType} onChangeText={setIncidentType} placeholder="e.g. DUI" />
        <TouchableOpacity style={styles.addButton} onPress={handleAdd}>
          <Text style={styles.addButtonText}>Add Alert Rule</Text>
        </TouchableOpacity>
      </View>
      <FlatList
        data={rules}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        renderItem={({ item }) => (
          <View style={styles.ruleCard}>
            <View style={styles.ruleRow}>
              <View>
                <Text style={styles.ruleCounty}>{item.county} County</Text>
                <Text style={styles.ruleType}>{item.incident_type}</Text>
              </View>
              <Switch value={item.enabled} onValueChange={(v) => handleToggle(item.id, v)} />
            </View>
            <TouchableOpacity onPress={() => handleRemove(item.id)}>
              <Text style={styles.removeText}>Remove</Text>
            </TouchableOpacity>
          </View>
        )}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No alert rules yet.</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: COLORS.background,
    padding: 20,
  },
  lockTitle: { fontSize: 20, fontWeight: '800', color: COLORS.primary, marginBottom: 8 },
  lockSubtext: { fontSize: 15, color: COLORS.secondary, textAlign: 'center' },
  form: { padding: 16, backgroundColor: COLORS.card, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  label: { fontSize: 13, fontWeight: '700', color: COLORS.secondary, textTransform: 'uppercase', marginBottom: 6, marginTop: 12 },
  input: {
    backgroundColor: COLORS.background,
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    color: COLORS.primary,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  addButton: {
    backgroundColor: COLORS.primary,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
    marginTop: 16,
  },
  addButtonText: { color: COLORS.card, fontSize: 15, fontWeight: '700' },
  list: { padding: 16 },
  ruleCard: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  ruleRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  ruleCounty: { fontSize: 15, fontWeight: '700', color: COLORS.primary },
  ruleType: { fontSize: 13, color: COLORS.secondary, marginTop: 2 },
  removeText: { fontSize: 13, color: COLORS.error, fontWeight: '600', marginTop: 8 },
  empty: { padding: 40, alignItems: 'center' },
  emptyText: { fontSize: 15, color: COLORS.secondary },
});
