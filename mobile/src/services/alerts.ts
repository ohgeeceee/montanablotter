import AsyncStorage from '@react-native-async-storage/async-storage';
import { getToken } from './auth';
import { alertProfilesApi, AlertProfile } from './me';

export interface AlertRule {
  id: string;
  county: string;
  incident_type: string;
  enabled: boolean;
}

const ALERTS_KEY = '@mb_alert_rules';

function profileToRule(profile: AlertProfile): AlertRule {
  return {
    id: String(profile.id),
    county: profile.counties?.[0] || '',
    incident_type: profile.alert_types?.[0] || '',
    enabled: profile.is_active,
  };
}

export async function getAlertRules(): Promise<AlertRule[]> {
  const token = await getToken();
  if (token) {
    try {
      const response = await alertProfilesApi.list();
      return response.profiles.map(profileToRule);
    } catch (err) {
      console.error('[Alerts] Failed to fetch from backend:', err);
      return [];
    }
  }
  const raw = await AsyncStorage.getItem(ALERTS_KEY);
  return raw ? JSON.parse(raw) : [];
}

export async function saveAlertRules(rules: AlertRule[]) {
  await AsyncStorage.setItem(ALERTS_KEY, JSON.stringify(rules));
}

export async function addAlertRule(rule: AlertRule) {
  const token = await getToken();
  if (token) {
    try {
      await alertProfilesApi.create({
        name: `${rule.county} ${rule.incident_type}`,
        counties: rule.county ? [rule.county] : [],
        alert_types: rule.incident_type ? [rule.incident_type] : ['all'],
        is_active: rule.enabled,
      });
      return;
    } catch (err) {
      console.error('[Alerts] Failed to add to backend:', err);
    }
  }
  const rules = await getAlertRules();
  rules.push(rule);
  await saveAlertRules(rules);
}

export async function removeAlertRule(id: string) {
  const token = await getToken();
  if (token) {
    try {
      await alertProfilesApi.remove(Number(id));
      return;
    } catch (err) {
      console.error('[Alerts] Failed to remove from backend:', err);
    }
  }
  const rules = await getAlertRules();
  await saveAlertRules(rules.filter((r) => r.id !== id));
}

export async function toggleAlertRule(id: string, enabled: boolean) {
  const token = await getToken();
  if (token) {
    try {
      await alertProfilesApi.update(Number(id), { is_active: enabled });
      return;
    } catch (err) {
      console.error('[Alerts] Failed to update backend:', err);
    }
  }
  const rules = await getAlertRules();
  const next = rules.map((r) => (r.id === id ? { ...r, enabled } : r));
  await saveAlertRules(next);
}
