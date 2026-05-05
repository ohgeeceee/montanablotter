import React, { useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';

import WatchlistScreen from './src/screens/WatchlistScreen';
import AlertsScreen from './src/screens/AlertsScreen';
import PremiumScreen from './src/screens/PremiumScreen';
import FeedScreen from './src/screens/FeedScreen';
import PostDetailScreen from './src/screens/PostDetailScreen';
import JailRostersScreen from './src/screens/JailRostersScreen';
import LawsScreen from './src/screens/LawsScreen';
import LawsDetailScreen from './src/screens/LawsDetailScreen';
import DiagnosticsScreen from './src/screens/DiagnosticsScreen';
import BlogScreen from './src/screens/BlogScreen';
import BlogPostScreen from './src/screens/BlogPostScreen';
import { LawCategory } from './src/types';
import * as Sentry from '@sentry/react-native';
import { initMonitoring } from './src/services/monitoring';
import { initPurchases } from './src/services/purchases';

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

const Tab = createBottomTabNavigator();
const FeedStack = createNativeStackNavigator<FeedStackParamList>();
const LawsStack = createNativeStackNavigator<LawsStackParamList>();
const BlogStack = createNativeStackNavigator<BlogStackParamList>();

function FeedStackNavigator() {
  return (
    <FeedStack.Navigator>
      <FeedStack.Screen name="FeedHome" component={FeedScreen} options={{ title: 'Montana Blotter' }} />
      <FeedStack.Screen name="PostDetail" component={PostDetailScreen} options={{ title: 'Post' }} />
      <FeedStack.Screen name="Watchlist" component={WatchlistScreen} options={{ title: 'Watchlist' }} />
      <FeedStack.Screen name="Alerts" component={AlertsScreen} options={{ title: 'Alerts' }} />
    </FeedStack.Navigator>
  );
}

function LawsStackNavigator() {
  return (
    <LawsStack.Navigator>
      <LawsStack.Screen name="LawsHome" component={LawsScreen} options={{ title: 'Montana Laws' }} />
      <LawsStack.Screen name="LawsDetail" component={LawsDetailScreen} options={{ title: 'Law Details' }} />
      <LawsStack.Screen name="Diagnostics" component={DiagnosticsScreen} options={{ title: 'Diagnostics' }} />
    </LawsStack.Navigator>
  );
}

function BlogStackNavigator() {
  return (
    <BlogStack.Navigator>
      <BlogStack.Screen name="BlogHome" component={BlogScreen} options={{ title: 'Blog' }} />
      <BlogStack.Screen name="BlogPost" component={BlogPostScreen} options={{ title: 'Article' }} />
    </BlogStack.Navigator>
  );
}

import { PremiumProvider } from './src/context/PremiumContext';

function App() {
  useEffect(() => {
    initPurchases().catch((err) => {
      console.error('[Purchases] Init failed:', err);
    });
  }, []);

  return (
    <PremiumProvider>
      <NavigationContainer>
        <StatusBar style="auto" />
        <Tab.Navigator
        sceneContainerStyle={{ backgroundColor: '#f8fafc' }}
        screenOptions={({ route }) => ({
          tabBarIcon: ({ focused, color, size }) => {
            let iconName: any = 'diamond';

            if (route.name === 'Feed') {
              iconName = focused ? 'newspaper' : 'newspaper-outline';
            } else if (route.name === 'Jail Rosters') {
              iconName = focused ? 'list' : 'list-outline';
            } else if (route.name === 'Laws') {
              iconName = focused ? 'book' : 'book-outline';
            } else if (route.name === 'Blog') {
              iconName = focused ? 'create' : 'create-outline';
            } else if (route.name === 'Premium') {
              iconName = focused ? 'diamond' : 'diamond-outline';
            }

            return <Ionicons name={iconName} size={size} color={color} />;
          },
          tabBarActiveTintColor: '#0f172a',
          tabBarInactiveTintColor: '#64748b',
          tabBarStyle: {
            height: 72,
            paddingTop: 8,
            paddingBottom: 8,
            borderTopColor: '#e2e8f0',
            backgroundColor: '#ffffff',
          },
          tabBarLabelStyle: {
            fontSize: 12,
            fontWeight: '700',
          },
          headerShown: false,
        })}
      >
        <Tab.Screen name="Feed" component={FeedStackNavigator} />
        <Tab.Screen name="Jail Rosters" component={JailRostersScreen} />
        <Tab.Screen name="Laws" component={LawsStackNavigator} />
        <Tab.Screen name="Blog" component={BlogStackNavigator} />
        <Tab.Screen name="Premium" component={PremiumScreen} />
      </Tab.Navigator>
      </NavigationContainer>
    </PremiumProvider>
  );
}

export default Sentry.wrap(App);
