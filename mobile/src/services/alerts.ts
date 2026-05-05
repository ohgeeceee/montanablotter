import AsyncStorage from '@react-native-async-storage/async-storage';

export interface AlertRule {
  id: string;
  county: string;
  incident_type: string;
  enabled: boolean;
}

const ALERTS_KEY = '@mb_alert_rules';

export async function getAlertRules(): Promise<AlertRule[]> {
  const raw = await AsyncStorage.getItem(ALERTS_KEY);
  return raw ? JSON.parse(raw) : [];
}

export async function saveAlertRules(rules: AlertRule[]) {
  await AsyncStorage.setItem(ALERTS_KEY, JSON.stringify(rules));
}

export async function addAlertRule(rule: AlertRule) {
  const rules = await getAlertRules();
  rules.push(rule);
  await saveAlertRules(rules);
}

export async function removeAlertRule(id: string) {
  const rules = await getAlertRules();
  await saveAlertRules(rules.filter((r) => r.id !== id));
}

export async function toggleAlertRule(id: string, enabled: boolean) {
  const rules = await getAlertRules();
  const next = rules.map((r) => (r.id === id ? { ...r, enabled } : r));
  await saveAlertRules(next);
}
