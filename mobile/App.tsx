import React, { useEffect } from 'react';
import { StatusBar, Platform, View } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import * as Sentry from '@sentry/react-native';

import MapScreen from './src/screens/MapScreen';
import WatchlistScreen from './src/screens/WatchlistScreen';
import AlertsScreen from './src/screens/AlertsScreen';
import PremiumScreen from './src/screens/PremiumScreen';
import FeedScreen from './src/screens/FeedScreen';
import PostDetailScreen from './src/screens/PostDetailScreen';
import JailRostersScreen from './src/screens/JailRostersScreen';
import LawsScreen from './src/screens/LawsScreen';
import LawsDetailScreen from './src/screens/LawsDetailScreen';
import DiagnosticsScreen from './src/screens/DiagnosticsScreen';
import MoreScreen from './src/screens/MoreScreen';
import BlogScreen from './src/screens/BlogScreen';
import BlogPostScreen from './src/screens/BlogPostScreen';
import LoginScreen from './src/screens/LoginScreen';
import RegisterScreen from './src/screens/RegisterScreen';
import AccountScreen from './src/screens/AccountScreen';
import WarrantsScreen from './src/screens/WarrantsScreen';
import CourtScreen from './src/screens/CourtScreen';
import PeopleScreen from './src/screens/PeopleScreen';
import { LawCategory } from './src/types';
import { initMonitoring } from './src/services/monitoring';
import { initPurchases } from './src/services/purchases';
import { initPushNotifications, addPushNotificationListeners, removePushNotificationListeners } from './src/services/push';
import { PremiumProvider } from './src/context/PremiumContext';
import { AuthProvider, useAuth } from './src/context/AuthContext';
import { colors, spacing } from './src/theme';

initMonitoring();

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
  Watchlist: undefined;
  Alerts: undefined;
};

type LawsStackParamList = {
  LawsHome: undefined;
  LawsDetail: { category: LawCategory };
  Diagnostics: undefined;
};

type BlogStackParamList = {
  BlogHome: undefined;
  BlogPost: { slug: string };
};

type AccountStackParamList = {
  AccountHome: undefined;
  Login: undefined;
  Register: undefined;
};

type MoreStackParamList = {
  MoreHome: undefined;
  Warrants: undefined;
  Court: undefined;
  People: undefined;
  Diagnostics: undefined;
};

const Tab = createBottomTabNavigator();
const FeedStack = createNativeStackNavigator<FeedStackParamList>();
const LawsStack = createNativeStackNavigator<LawsStackParamList>();
const BlogStack = createNativeStackNavigator<BlogStackParamList>();
const AccountStack = createNativeStackNavigator<AccountStackParamList>();
const MoreStack = createNativeStackNavigator<MoreStackParamList>();

function FeedStackNavigator() {
  return (
    <FeedStack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <FeedStack.Screen name="FeedHome" component={FeedScreen} />
      <FeedStack.Screen name="PostDetail" component={PostDetailScreen} options={{ title: 'Incident' }} />
      <FeedStack.Screen name="Watchlist" component={WatchlistScreen} options={{ title: 'Watchlist' }} />
      <FeedStack.Screen name="Alerts" component={AlertsScreen} options={{ title: 'Alerts' }} />
    </FeedStack.Navigator>
  );
}

function LawsStackNavigator() {
  return (
    <LawsStack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <LawsStack.Screen name="LawsHome" component={LawsScreen} />
      <LawsStack.Screen name="LawsDetail" component={LawsDetailScreen} options={{ title: 'Law Details' }} />
    </LawsStack.Navigator>
  );
}

function BlogStackNavigator() {
  return (
    <BlogStack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <BlogStack.Screen name="BlogHome" component={BlogScreen} />
      <BlogStack.Screen name="BlogPost" component={BlogPostScreen} options={{ title: 'Article' }} />
    </BlogStack.Navigator>
  );
}

function AccountStackNavigator() {
  return (
    <AccountStack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <AccountStack.Screen name="AccountHome" component={AccountScreen} />
      <AccountStack.Screen name="Login" component={LoginScreen} />
      <AccountStack.Screen name="Register" component={RegisterScreen} />
    </AccountStack.Navigator>
  );
}

function MoreStackNavigator() {
  return (
    <MoreStack.Navigator
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <MoreStack.Screen name="MoreHome" component={MoreScreen} />
      <MoreStack.Screen name="Warrants" component={WarrantsScreen} options={{ title: 'Warrants' }} />
      <MoreStack.Screen name="Court" component={CourtScreen} options={{ title: 'Court Lookup' }} />
      <MoreStack.Screen name="People" component={PeopleScreen} options={{ title: 'Missing Persons' }} />
      <MoreStack.Screen name="Diagnostics" component={DiagnosticsScreen} options={{ title: 'Diagnostics' }} />
    </MoreStack.Navigator>
  );
}

const TAB_BAR_HEIGHT = Platform.OS === 'android' ? 72 : 90;

function MainTabNavigator() {
  return (
    <Tab.Navigator
      sceneContainerStyle={{ backgroundColor: colors.background }}
      screenOptions={({ route }) => ({
        tabBarIcon: ({ focused, color, size }) => {
          let iconName: keyof typeof Ionicons.glyphMap = 'newspaper-outline';

          if (route.name === 'Feed') {
            iconName = focused ? 'newspaper' : 'newspaper-outline';
          } else if (route.name === 'Jail') {
            iconName = focused ? 'list' : 'list-outline';
          } else if (route.name === 'Laws') {
            iconName = focused ? 'book' : 'book-outline';
          } else if (route.name === 'Blog') {
            iconName = focused ? 'create' : 'create-outline';
          } else if (route.name === 'Map') {
            iconName = focused ? 'map' : 'map-outline';
          } else if (route.name === 'More') {
            iconName = focused ? 'apps' : 'apps-outline';
          } else if (route.name === 'Premium') {
            iconName = focused ? 'star' : 'star-outline';
          } else if (route.name === 'Account') {
            iconName = focused ? 'person' : 'person-outline';
          }

          return <Ionicons name={iconName} size={size} color={color} />;
        },
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarStyle: {
          height: TAB_BAR_HEIGHT,
          paddingTop: spacing[2],
          paddingBottom: Platform.OS === 'android' ? spacing[2] : spacing[4],
          borderTopColor: colors.border,
          backgroundColor: colors.card,
          elevation: 8,
          shadowColor: '#000',
          shadowOffset: { width: 0, height: -2 },
          shadowOpacity: 0.05,
          shadowRadius: 4,
        },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: '700',
        },
        headerShown: false,
      })}
    >
      <Tab.Screen name="Feed" component={FeedStackNavigator} />
      <Tab.Screen name="Jail" component={JailRostersScreen} options={{ tabBarLabel: 'Jail' }} />
      <Tab.Screen name="Laws" component={LawsStackNavigator} />
      <Tab.Screen name="Blog" component={BlogStackNavigator} />
      <Tab.Screen name="Map" component={MapScreen} />
      <Tab.Screen name="More" component={MoreStackNavigator} />
      <Tab.Screen name="Premium" component={PremiumScreen} />
      <Tab.Screen name="Account" component={AccountStackNavigator} />
    </Tab.Navigator>
  );
}

function App() {
  useEffect(() => {
    initPurchases().catch((err) => {
      console.error('[Purchases] Init failed:', err);
    });
  }, []);

  useEffect(() => {
    let mounted = true;
    let subscription: { subscription: any; responseSubscription: any } | null = null;

    initPushNotifications()
      .then((token) => {
        if (mounted && token) {
          subscription = addPushNotificationListeners(
            (notification) => {
              console.log('[Push] Received:', notification);
            },
            (response) => {
              console.log('[Push] Response:', response);
            },
          );
        }
      })
      .catch((err) => {
        console.error('[Push] Init failed:', err);
      });

    return () => {
      mounted = false;
      if (subscription) {
        removePushNotificationListeners(subscription.subscription, subscription.responseSubscription);
      }
    };
  }, []);

  return (
    <AuthProvider>
      <PremiumProvider>
        <NavigationContainer>
          <StatusBar barStyle="light-content" backgroundColor={colors.primary} translucent={false} />
          <View style={{ flex: 1, backgroundColor: colors.background }}>
            <MainTabNavigator />
          </View>
        </NavigationContainer>
      </PremiumProvider>
    </AuthProvider>
  );
}

export default Sentry.wrap(App);
