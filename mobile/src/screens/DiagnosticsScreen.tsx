import React, { useState } from 'react';
import {
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { COLORS } from '../constants';
import { getMonitoringStatus, sendTestMonitoringEvent } from '../services/monitoring';

export default function DiagnosticsScreen() {
  const [submitting, setSubmitting] = useState<'message' | 'exception' | null>(null);
  const monitoring = getMonitoringStatus();

  const handleSend = async (kind: 'message' | 'exception') => {
    try {
      setSubmitting(kind);
      await sendTestMonitoringEvent(kind);
      Alert.alert('Diagnostic sent', `Queued a Sentry test ${kind}. Check the Sentry project for delivery.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown diagnostics error';
      Alert.alert('Unable to send diagnostic', message);
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.hero}>
        <Text style={styles.title}>Diagnostics</Text>
        <Text style={styles.subtitle}>
          Hidden release diagnostics for mobile QA and launch verification.
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Monitoring status</Text>
        <Text style={styles.row}>
          Sentry DSN: <Text style={styles.value}>{monitoring.dsnConfigured ? 'Configured' : 'Missing'}</Text>
        </Text>
        <Text style={styles.row}>
          Environment: <Text style={styles.value}>{monitoring.environment}</Text>
        </Text>
        <Text style={styles.row}>
          Current build sending: <Text style={styles.value}>{monitoring.enabledInCurrentBuild ? 'Yes' : 'No'}</Text>
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Test events</Text>
        <Text style={styles.helpText}>
          Use these buttons from an internal or store-signed build to confirm Sentry delivery before launch.
        </Text>
        <TouchableOpacity
          style={[styles.button, submitting === 'message' && styles.buttonDisabled]}
          disabled={submitting !== null}
          onPress={() => handleSend('message')}
        >
          <Text style={styles.buttonText}>
            {submitting === 'message' ? 'Sending message...' : 'Send test message'}
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.button, styles.buttonSecondary, submitting === 'exception' && styles.buttonDisabled]}
          disabled={submitting !== null}
          onPress={() => handleSend('exception')}
        >
          <Text style={styles.buttonText}>
            {submitting === 'exception' ? 'Sending exception...' : 'Send test exception'}
          </Text>
        </TouchableOpacity>
      </View>

      <View style={styles.notice}>
        <Text style={styles.noticeTitle}>Access</Text>
        <Text style={styles.noticeText}>
          This screen is intentionally hidden. Open the Laws tab and tap the legal disclaimer title seven times.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  content: {
    padding: 16,
    gap: 16,
  },
  hero: {
    backgroundColor: COLORS.primary,
    borderRadius: 16,
    padding: 20,
  },
  title: {
    color: COLORS.card,
    fontSize: 24,
    fontWeight: '700',
    marginBottom: 8,
  },
  subtitle: {
    color: '#cbd5e1',
    fontSize: 14,
    lineHeight: 20,
  },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  cardTitle: {
    color: COLORS.primary,
    fontSize: 16,
    fontWeight: '700',
    marginBottom: 12,
  },
  row: {
    color: COLORS.secondary,
    fontSize: 14,
    marginBottom: 8,
  },
  value: {
    color: COLORS.primary,
    fontWeight: '600',
  },
  helpText: {
    color: COLORS.secondary,
    fontSize: 14,
    lineHeight: 20,
    marginBottom: 16,
  },
  button: {
    backgroundColor: COLORS.primary,
    borderRadius: 10,
    paddingVertical: 14,
    paddingHorizontal: 16,
    marginBottom: 12,
    alignItems: 'center',
  },
  buttonSecondary: {
    backgroundColor: COLORS.error,
    marginBottom: 0,
  },
  buttonDisabled: {
    opacity: 0.6,
  },
  buttonText: {
    color: COLORS.card,
    fontSize: 14,
    fontWeight: '700',
  },
  notice: {
    backgroundColor: '#eef2ff',
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: '#c7d2fe',
  },
  noticeTitle: {
    color: '#3730a3',
    fontSize: 14,
    fontWeight: '700',
    marginBottom: 8,
  },
  noticeText: {
    color: '#4338ca',
    fontSize: 13,
    lineHeight: 19,
  },
});
