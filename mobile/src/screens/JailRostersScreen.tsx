import React, { useState } from 'react';
import {
  View,
  Text,
  FlatList,
  TouchableOpacity,
  Linking,
  TextInput,
  StyleSheet,
} from 'react-native';
import { JailRoster } from '../types';
import { COLORS } from '../constants';

const JAIL_ROSTERS: JailRoster[] = [
  { name: 'Beaverhead', url: 'https://beaverheadcountymt.gov/departments/sheriff/', phone: '406-683-3700', hasOnline: true },
  { name: 'Big Horn', url: 'https://www.bighorncountymt.gov/239/Detention', phone: '406-665-9780', hasOnline: true },
  { name: 'Blaine', url: null, phone: '406-357-3260', hasOnline: false },
  { name: 'Broadwater', url: 'https://www.broadwatercountysheriff.org/roster.php', phone: '406-266-3445', hasOnline: true },
  { name: 'Carbon', url: 'https://carbonmt.gov/sheriff/', phone: '406-446-1234', hasOnline: true },
  { name: 'Carter', url: null, phone: '406-775-8741', hasOnline: false },
  { name: 'Cascade', url: 'https://www.cascadecountymt.gov/314/Inmate-Roster', phone: '406-454-6840', hasOnline: true },
  { name: 'Chouteau', url: null, phone: '406-622-3660', hasOnline: false },
  { name: 'Custer', url: 'https://www.custercountysheriff.com/inmate-search', phone: '406-874-3300', hasOnline: true },
  { name: 'Daniels', url: null, phone: '406-487-2691', hasOnline: false },
  { name: 'Dawson', url: 'https://www.dawsoncountymontana.com/sheriff', phone: '406-377-7600', hasOnline: true },
  { name: 'Deer Lodge', url: null, phone: '406-563-5421', hasOnline: false },
  { name: 'Fallon', url: null, phone: '406-778-2879', hasOnline: false },
  { name: 'Fergus', url: 'https://fergusmt.gov/detention-center-roster', phone: '406-535-3860', hasOnline: true },
  { name: 'Flathead', url: 'https://apps.flathead.mt.gov/jailroster/', phone: '406-758-5610', hasOnline: true },
  { name: 'Gallatin', url: 'https://gallatin-so-mt.zuercherportal.com/#/inmates', phone: '406-582-2100', hasOnline: true },
  { name: 'Garfield', url: null, phone: '406-557-2540', hasOnline: false },
  { name: 'Glacier', url: 'https://glaciercountymt.gov/category/jail-roster/', phone: '406-873-4600', hasOnline: true },
  { name: 'Golden Valley', url: null, phone: '406-568-2321', hasOnline: false },
  { name: 'Granite', url: 'https://granitecountyjail.org/', phone: '406-859-3771', hasOnline: true },
  { name: 'Hill', url: 'https://vinelink.vineapps.com/state/mt', phone: '406-265-5481', hasOnline: true },
  { name: 'Jefferson', url: 'https://www.jeffersoncountysheriffmt.gov/', phone: '406-225-4075', hasOnline: true },
  { name: 'Judith Basin', url: null, phone: '406-535-3860', hasOnline: false },
  { name: 'Lake', url: null, phone: '406-883-7301', hasOnline: false },
  { name: 'Lewis and Clark', url: 'https://www.lccountymt.gov/Sheriff/Detention-Center', phone: '406-447-8270', hasOnline: true },
  { name: 'Liberty', url: null, phone: '406-759-5171', hasOnline: false },
  { name: 'Lincoln', url: 'http://inmateroster.lincolncountysheriff.us/', phone: '406-293-0242', hasOnline: true },
  { name: 'Madison', url: 'https://webportal.mcits.site/NewWorld.InmateInquiry/MadisonCountyJail', phone: '406-843-5351', hasOnline: true },
  { name: 'McCone', url: null, phone: '406-485-3405', hasOnline: false },
  { name: 'Meagher', url: null, phone: '406-547-3397', hasOnline: false },
  { name: 'Mineral', url: 'https://co.mineral.mt.us/departments/sheriff/', phone: '406-822-3534', hasOnline: true },
  { name: 'Missoula', url: 'https://webapps.missoulacounty.us/jailroster/Inmates', phone: '406-258-4780', hasOnline: true },
  { name: 'Musselshell', url: null, phone: '406-323-1122', hasOnline: false },
  { name: 'Park', url: 'https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/', phone: '406-222-4172', hasOnline: true },
  { name: 'Petroleum', url: null, phone: '406-429-6551', hasOnline: false },
  { name: 'Phillips', url: 'https://phillipscosheriff.com/inmates/', phone: '406-654-2020', hasOnline: true },
  { name: 'Pondera', url: 'https://ponderacountyjail.org/inmate-search/', phone: '406-271-4100', hasOnline: true },
  { name: 'Powder River', url: null, phone: '406-436-2260', hasOnline: false },
  { name: 'Powell', url: 'https://www.powellcountymt.gov/sheriff/page/detention-facility', phone: '406-846-2711', hasOnline: true },
  { name: 'Prairie', url: null, phone: '406-635-5738', hasOnline: false },
  { name: 'Ravalli', url: 'https://ravallicounty.gov/239/Adult-Detention-Center', phone: '406-375-4060', hasOnline: true },
  { name: 'Richland', url: null, phone: '406-433-2919', hasOnline: false },
  { name: 'Roosevelt', url: null, phone: '406-653-6230', hasOnline: false },
  { name: 'Rosebud', url: null, phone: '406-346-2715', hasOnline: false },
  { name: 'Sanders', url: 'https://sanders-mt.publiclogs.com/', phone: '406-827-3584', hasOnline: true },
  { name: 'Sheridan', url: null, phone: '406-765-1200', hasOnline: false },
  { name: 'Silver Bow', url: 'https://co.silverbow.mt.us/3274/Detention-Center', phone: '406-497-1120', hasOnline: true },
  { name: 'Stillwater', url: null, phone: '406-322-5326', hasOnline: false },
  { name: 'Sweet Grass', url: null, phone: '406-932-5143', hasOnline: false },
  { name: 'Teton', url: null, phone: '406-466-5781', hasOnline: false },
  { name: 'Toole', url: null, phone: '406-434-5585', hasOnline: false },
  { name: 'Treasure', url: null, phone: '406-342-5211', hasOnline: false },
  { name: 'Valley', url: 'https://www.valleycountymt.gov/1288/Jail-Roster', phone: '406-228-9355', hasOnline: true },
  { name: 'Wheatland', url: null, phone: '406-632-4311', hasOnline: false },
  { name: 'Wibaux', url: null, phone: '406-796-2415', hasOnline: false },
  { name: 'Yellowstone', url: 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp', phone: '406-256-2929', hasOnline: true },
];

export default function JailRostersScreen() {
  const [search, setSearch] = useState('');

  const filteredRosters = JAIL_ROSTERS.filter(roster =>
    roster.name.toLowerCase().includes(search.toLowerCase())
  );

  const openUrl = (url: string) => {
    Linking.openURL(url).catch(err => console.error('Failed to open URL:', err));
  };

  const callPhone = (phone: string) => {
    Linking.openURL(`tel:${phone}`).catch(err => console.error('Failed to make call:', err));
  };

  const renderRoster = ({ item }: { item: JailRoster }) => (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.countyName}>{item.name} County</Text>
        <View style={[styles.badge, item.hasOnline ? styles.badgeOnline : styles.badgePhone]}>
          <Text style={[styles.badgeText, item.hasOnline ? styles.badgeTextOnline : styles.badgeTextPhone]}>
            {item.hasOnline ? 'Online' : 'Phone Only'}
          </Text>
        </View>
      </View>
      <Text style={styles.subtext}>{item.name} County Sheriff's Office</Text>
      <View style={styles.buttonContainer}>
        <TouchableOpacity
          style={[styles.button, item.hasOnline ? styles.buttonPrimary : styles.buttonSecondary]}
          onPress={() => openUrl(item.url || 'https://vinelink.vineapps.com/state/mt')}
        >
          <Text style={[styles.buttonText, item.hasOnline ? styles.buttonTextPrimary : styles.buttonTextSecondary]}>
            {item.hasOnline ? 'View Roster' : 'VINELink'}
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.phoneButton}
          onPress={() => callPhone(item.phone)}
        >
          <Text style={styles.phoneButtonText}>📞 {item.phone}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Montana Jail Rosters</Text>
        <Text style={styles.headerSubtitle}>
          Links to all 56 county jail rosters and inmate search pages
        </Text>
      </View>
      <View style={styles.searchContainer}>
        <TextInput
          style={styles.searchInput}
          placeholder="Search county..."
          value={search}
          onChangeText={setSearch}
          placeholderTextColor={COLORS.secondary}
        />
      </View>
      <FlatList
        data={filteredRosters}
        keyExtractor={item => item.name}
        renderItem={renderRoster}
        contentContainerStyle={styles.list}
        showsVerticalScrollIndicator={false}
        ListHeaderComponent={
          <View style={styles.stateLinks}>
            <TouchableOpacity onPress={() => openUrl('https://offendersearch.mt.gov/conweb/')}>
              <Text style={styles.stateLink}>MT Dept. of Corrections Search</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => openUrl('https://vinelink.vineapps.com/state/mt')}>
              <Text style={styles.stateLink}>VINELink Statewide</Text>
            </TouchableOpacity>
          </View>
        }
      />
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
    paddingTop: 60,
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
  },
  searchContainer: {
    padding: 16,
    backgroundColor: COLORS.card,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  searchInput: {
    backgroundColor: COLORS.background,
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    color: COLORS.primary,
  },
  list: {
    padding: 16,
  },
  stateLinks: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginBottom: 16,
  },
  stateLink: {
    color: COLORS.info,
    fontSize: 13,
    fontWeight: '600',
  },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  countyName: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.primary,
  },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  badgeOnline: {
    backgroundColor: '#dcfce7',
  },
  badgePhone: {
    backgroundColor: '#f1f5f9',
  },
  badgeText: {
    fontSize: 11,
    fontWeight: '600',
  },
  badgeTextOnline: {
    color: '#15803d',
  },
  badgeTextPhone: {
    color: COLORS.secondary,
  },
  subtext: {
    fontSize: 12,
    color: COLORS.secondary,
    marginBottom: 12,
  },
  buttonContainer: {
    flexDirection: 'row',
    gap: 8,
  },
  button: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
  },
  buttonPrimary: {
    backgroundColor: COLORS.accent,
  },
  buttonSecondary: {
    backgroundColor: '#e2e8f0',
  },
  buttonText: {
    fontSize: 14,
    fontWeight: '600',
  },
  buttonTextPrimary: {
    color: COLORS.card,
  },
  buttonTextSecondary: {
    color: COLORS.primary,
  },
  phoneButton: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  phoneButtonText: {
    fontSize: 13,
    fontWeight: '600',
    color: COLORS.secondary,
  },
});
