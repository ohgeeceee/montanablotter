import React, { useEffect, useRef, useState } from 'react';
import {
  Alert,
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  TextInput,
} from 'react-native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useNavigation } from '@react-navigation/native';
import { LawCategory } from '../types';
import { COLORS } from '../constants';

const LAW_CATEGORIES: LawCategory[] = [
  {
    id: 'criminal',
    title: 'Criminal Laws',
    subtitle: 'Title 45 — Montana Code Annotated',
    icon: '⚖️',
    color: '#ef4444',
    laws: [
      { id: 'assault', title: 'Assault (MCA 45-5-201)', description: 'Purposely or knowingly causing bodily injury to another person. Simple assault is a misdemeanor. Aggravated assault (with a weapon or serious bodily injury) is a felony punishable by up to 20 years.' },
      { id: 'homicide', title: 'Homicide (MCA 45-5-101 to 103)', description: 'Deliberate homicide (first degree murder) carries a sentence of death or life imprisonment. Mitigated deliberate homicide and negligent homicide carry lesser penalties depending on intent and circumstances.' },
      { id: 'theft', title: 'Theft (MCA 45-6-301)', description: 'Theft of property under $1,500 is a misdemeanor. Over $1,500 is a felony. Grand theft of property over $5,000 carries up to 10 years imprisonment. Motor vehicle theft is always a felony.' },
      { id: 'burglary', title: 'Burglary (MCA 45-6-204)', description: 'Entering or remaining in an occupied structure with intent to commit a crime is burglary (felony, up to 20 years). Aggravated burglary involving a weapon carries up to 40 years.' },
      { id: 'drugs', title: 'Drug Offenses (MCA 45-9)', description: 'Possession of dangerous drugs varies by schedule. Possession of up to 60g of marijuana is a misdemeanor; over that amount or intent to distribute is a felony. Trafficking controlled substances carries up to life imprisonment depending on the substance.' },
      { id: 'dui', title: 'DUI (MCA 61-8-401)', description: 'Operating a vehicle with a BAC of 0.08% or higher (0.04% for CDL holders). First offense is a misdemeanor with a minimum 24-hour jail term. Repeat offenses escalate to felonies. Aggravated DUI (BAC ≥ 0.16%) carries enhanced penalties.' },
      { id: 'sexual', title: 'Sexual Assault (MCA 45-5-502)', description: 'Sexual assault without consent is a felony punishable by up to life imprisonment. Rape and sexual intercourse without consent carry mandatory minimum sentences based on victim age and circumstances.' },
      { id: 'domestic', title: 'Domestic Violence (MCA 45-5-206)', description: 'Partner or family member assault is a misdemeanor on first offense, felony on third. Violation of a restraining order in a domestic violence context is a separate criminal offense.' },
    ],
  },
  {
    id: 'traffic',
    title: 'Traffic Laws',
    subtitle: 'Title 61 — Montana Code Annotated',
    icon: '🚗',
    color: '#f59e0b',
    laws: [
      { id: 'speed', title: 'Speed Limits (MCA 61-8-303)', description: "Montana's default speed limits: 80 mph on interstate highways, 70 mph on two-lane paved roads, 25 mph in urban districts, and 15 mph in school zones. Excessive speeding (25+ mph over limit) can result in felony charges if it causes death or serious injury." },
      { id: 'seatbelt', title: 'Seat Belt Law (MCA 61-9-409)', description: 'All front-seat occupants must wear a seat belt. Children under 6 or under 60 lbs must be in an approved child safety seat. Violations are a primary offense — officers can pull you over solely for seat belt non-compliance.' },
      { id: 'distracted', title: 'Distracted Driving (MCA 61-8-1011)', description: 'Texting while driving is illegal statewide. Handheld cell phone use while driving is prohibited. First offense is a $100 fine; fines increase for subsequent offenses.' },
      { id: 'impaired', title: 'DUI / Impaired Driving (MCA 61-8-401)', description: 'BAC limit is 0.08% for standard drivers, 0.04% for commercial drivers, and 0.02% for drivers under 21. Implied consent law means refusal to take a breath/blood test results in automatic license suspension.' },
      { id: 'reckless', title: 'Reckless Driving (MCA 61-8-301)', description: 'Operating a vehicle with willful disregard for safety of persons or property is a misdemeanor. Negligent vehicular assault (causing injury) is a felony punishable by up to 10 years.' },
      { id: 'insurance', title: 'Insurance Requirements (MCA 61-6-301)', description: 'All registered vehicles must carry minimum liability insurance: $25,000 per person / $50,000 per accident for bodily injury, and $20,000 for property damage. Driving uninsured is a misdemeanor.' },
      { id: 'open', title: 'Open Container (MCA 61-8-460)', description: 'Possession of an open alcoholic beverage container in a motor vehicle on a public highway is illegal for both driver and passengers. Violation is a misdemeanor.' },
      { id: 'fleeing', title: 'Fleeing or Eluding (MCA 61-8-316)', description: 'Willfully failing to stop when signaled by law enforcement is a misdemeanor. If the pursuit results in injury or death, charges escalate to a felony.' },
    ],
  },
  {
    id: 'hunting',
    title: 'Hunting & Fishing',
    subtitle: 'Title 87 — Montana Code Annotated',
    icon: '🎯',
    color: '#22c55e',
    laws: [
      { id: 'license', title: 'License Requirements (MCA 87-2-102)', description: 'Any person hunting, trapping, or fishing in Montana must have a valid license issued by Montana Fish, Wildlife & Parks (FWP). Residents and non-residents have different license fees and tag requirements.' },
      { id: 'orange', title: 'Hunter Orange (MCA 87-6-414)', description: 'During firearms deer and elk seasons, hunters must wear at least 400 square inches of fluorescent orange on the head and upper body. Violation is a misdemeanor.' },
      { id: 'trespass', title: 'Trespassing to Hunt (MCA 87-6-206)', description: 'Hunting or fishing on private land without landowner permission is illegal and results in license suspension in addition to trespass penalties. Written permission is recommended.' },
      { id: 'poaching', title: 'Poaching (MCA 87-6-901)', description: 'Taking wildlife out of season, over bag limits, or without a license constitutes poaching. Penalties include fines up to $10,000, license revocation, and possible felony charges for trophy species like elk, moose, or bighorn sheep.' },
      { id: 'baiting', title: 'Baiting & Feeding Wildlife (MCA 87-6-209)', description: 'Baiting deer, elk, or other big game is prohibited. Knowingly feeding wildlife in a manner that creates a public safety hazard is a misdemeanor.' },
      { id: 'fishing', title: 'Fishing Regulations (MCA 87-3-101)', description: 'Fishing without a license, exceeding bag limits, or using prohibited methods (dynamite, chemicals, electricity) are all illegal. Specific stream and lake regulations vary — check FWP regulations annually.' },
      { id: 'sunday', title: 'Sunday Hunting', description: 'Montana has no general prohibition on Sunday hunting. However, specific seasons and regulations apply per species. Always verify current FWP regulations before hunting.' },
      { id: 'firearm', title: 'Firearm Use While Hunting (MCA 87-6-401)', description: 'Discharging a firearm from a public road or within 50 feet of the center of a public road is prohibited. Shooting across a highway or railway is illegal.' },
    ],
  },
  {
    id: 'general',
    title: 'General Statutes',
    subtitle: 'Montana Code Annotated — Selected Titles',
    icon: '📖',
    color: '#6366f1',
    laws: [
      { id: 'arms', title: 'Right to Bear Arms (MT Const. Art. II §12)', description: "Montana's constitution explicitly protects the right of any person to bear arms in defense of home, person, and property. Montana imposes fewer restrictions than federal minimums on firearms ownership for law-abiding residents." },
      { id: 'concealed', title: 'Concealed Carry (MCA 45-8-316)', description: 'Montana is a permitless carry (constitutional carry) state. No permit is required to carry a concealed firearm for eligible adults. A permit is available voluntarily for reciprocity purposes in other states.' },
      { id: 'selfdefense', title: 'Self-Defense / Stand Your Ground (MCA 45-3-102)', description: 'A person is justified in using force to defend themselves or others when they reasonably believe force is necessary. Montana does not require a duty to retreat when facing unlawful force.' },
      { id: 'criminal_trespass', title: 'Trespassing (MCA 45-6-203)', description: 'Criminal trespass is knowingly entering or remaining on another\'s land after being notified not to. Posted signs, fencing, or verbal/written notice all constitute adequate warning. Violation is a misdemeanor.' },
      { id: 'intoxication', title: 'Public Intoxication (MCA 45-5-624)', description: 'Being intoxicated in a public place is not a criminal offense in Montana. However, law enforcement may take an intoxicated person into protective custody for their safety.' },
      { id: 'privacy', title: 'Montana Privacy Law (MCA 45-8-213)', description: 'Intercepting private communications without consent, voyeurism, and unlawful use of surveillance equipment are criminal offenses punishable as felonies depending on severity.' },
      { id: 'marijuana', title: 'Marijuana Laws (MCA 16-12)', description: 'Recreational marijuana use is legal for adults 21+ following the passage of I-190 in 2020. Adults may possess up to 1 ounce in public and grow up to 2 plants at home. Sales require a licensed dispensary. Use in public or in a vehicle remains prohibited.' },
      { id: 'openrange', title: 'Property Rights & Open Range (MCA 81-4-101)', description: "Montana is an open-range state in many rural areas. Livestock have the right to roam on unenclosed land. Motorists who hit livestock on open-range roads may be liable for the animal's value." },
    ],
  },
];

type LawsStackParamList = {
  LawsHome: undefined;
  LawsDetail: { category: LawCategory };
  Diagnostics: undefined;
};

export default function LawsScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<LawsStackParamList>>();
  const [diagnosticTapCount, setDiagnosticTapCount] = useState(0);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    return () => {
      if (resetTimerRef.current) {
        clearTimeout(resetTimerRef.current);
      }
    };
  }, []);

  const handleDiagnosticTap = () => {
    const nextCount = diagnosticTapCount + 1;
    setDiagnosticTapCount(nextCount);

    if (resetTimerRef.current) {
      clearTimeout(resetTimerRef.current);
    }

    if (nextCount >= 7) {
      setDiagnosticTapCount(0);
      navigation.navigate('Diagnostics');
      return;
    }

    resetTimerRef.current = setTimeout(() => {
      setDiagnosticTapCount(0);
    }, 1500);

    if (nextCount === 4) {
      Alert.alert('Diagnostics shortcut', 'Tap the legal disclaimer title three more times to open diagnostics.');
    }
  };

  const filteredCategories = LAW_CATEGORIES.filter((category) => {
    const query = search.trim().toLowerCase();

    if (!query) {
      return true;
    }

    return (
      category.title.toLowerCase().includes(query) ||
      category.subtitle.toLowerCase().includes(query) ||
      category.laws.some((law) =>
        `${law.title} ${law.description}`.toLowerCase().includes(query)
      )
    );
  });

  const totalLawCount = LAW_CATEGORIES.reduce((sum, category) => sum + category.laws.length, 0);

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerEyebrow}>Reference Guide</Text>
        <Text style={styles.headerTitle}>Montana Laws</Text>
        <Text style={styles.headerSubtitle}>
          A plain-language overview of key Montana statutes
        </Text>
        <View style={styles.headerStats}>
          <View style={styles.headerStatCard}>
            <Text style={styles.headerStatValue}>{LAW_CATEGORIES.length}</Text>
            <Text style={styles.headerStatLabel}>Categories</Text>
          </View>
          <View style={styles.headerStatCard}>
            <Text style={styles.headerStatValue}>{totalLawCount}</Text>
            <Text style={styles.headerStatLabel}>Summaries</Text>
          </View>
        </View>
      </View>

      <View style={styles.content}>
        <TextInput
          style={styles.searchInput}
          placeholder="Search laws, topics, or statutes..."
          value={search}
          onChangeText={setSearch}
          placeholderTextColor={COLORS.secondary}
        />
        {filteredCategories.map(category => (
          <TouchableOpacity
            key={category.id}
            style={styles.card}
            onPress={() => navigation.navigate('LawsDetail', { category })}
          >
            <View style={[styles.iconContainer, { backgroundColor: category.color + '20' }]}>
              <Text style={styles.icon}>{category.icon}</Text>
            </View>
            <View style={styles.cardContent}>
              <Text style={styles.cardTitle}>{category.title}</Text>
              <Text style={styles.cardSubtitle}>{category.subtitle}</Text>
              <Text style={styles.cardCount}>{category.laws.length} laws</Text>
            </View>
            <Text style={styles.arrow}>→</Text>
          </TouchableOpacity>
        ))}
        {filteredCategories.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyTitle}>No law summaries matched that search.</Text>
            <Text style={styles.emptyText}>Try a broader term like "DUI", "theft", or "hunting".</Text>
          </View>
        ) : null}
      </View>

      <View style={styles.disclaimer}>
        <TouchableOpacity onPress={handleDiagnosticTap} activeOpacity={0.8}>
          <Text style={styles.disclaimerTitle}>Legal Disclaimer</Text>
        </TouchableOpacity>
        <Text style={styles.disclaimerText}>
          This page is for general informational purposes only and does not constitute legal advice. 
          Always verify current statutes at leg.mt.gov or consult a licensed Montana attorney.
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
  header: {
    backgroundColor: COLORS.primary,
    padding: 20,
    paddingTop: 24,
  },
  headerEyebrow: {
    fontSize: 12,
    fontWeight: '700',
    color: '#93c5fd',
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
  headerStats: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 16,
  },
  headerStatCard: {
    minWidth: 100,
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
  },
  headerStatValue: {
    fontSize: 20,
    fontWeight: '800',
    color: COLORS.card,
    marginBottom: 2,
  },
  headerStatLabel: {
    fontSize: 11,
    color: '#cbd5e1',
    textTransform: 'uppercase',
    fontWeight: '700',
    letterSpacing: 0.6,
  },
  content: {
    padding: 16,
  },
  searchInput: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 14,
    fontSize: 15,
    color: COLORS.primary,
    borderWidth: 1,
    borderColor: COLORS.border,
    marginBottom: 14,
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
  iconContainer: {
    width: 48,
    height: 48,
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 12,
  },
  icon: {
    fontSize: 24,
  },
  cardContent: {
    flex: 1,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
    marginBottom: 2,
  },
  cardSubtitle: {
    fontSize: 12,
    color: COLORS.secondary,
    marginBottom: 4,
  },
  cardCount: {
    fontSize: 11,
    color: COLORS.info,
    fontWeight: '600',
  },
  arrow: {
    fontSize: 18,
    color: COLORS.secondary,
  },
  emptyState: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 20,
    alignItems: 'center',
  },
  emptyTitle: {
    color: COLORS.primary,
    fontSize: 15,
    fontWeight: '700',
    marginBottom: 6,
    textAlign: 'center',
  },
  emptyText: {
    color: COLORS.secondary,
    fontSize: 13,
    lineHeight: 19,
    textAlign: 'center',
  },
  disclaimer: {
    margin: 16,
    padding: 16,
    backgroundColor: '#fef3c7',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#fcd34d',
  },
  disclaimerTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#92400e',
    marginBottom: 8,
  },
  disclaimerText: {
    fontSize: 13,
    color: '#92400e',
    lineHeight: 20,
  },
});
