import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';

import FeedScreen from './src/screens/FeedScreen';
import PostDetailScreen from './src/screens/PostDetailScreen';
import JailRostersScreen from './src/screens/JailRostersScreen';
import LawsScreen from './src/screens/LawsScreen';
import LawsDetailScreen from './src/screens/LawsDetailScreen';
import BlogScreen from './src/screens/BlogScreen';
import BlogPostScreen from './src/screens/BlogPostScreen';
import { LawCategory } from './src/types';

type FeedStackParamList = {
  FeedHome: undefined;
  PostDetail: { postId: number };
};

type LawsStackParamList = {
  LawsHome: undefined;
  LawsDetail: { category: LawCategory };
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
    </FeedStack.Navigator>
  );
}

function LawsStackNavigator() {
  return (
    <LawsStack.Navigator>
      <LawsStack.Screen name="LawsHome" component={LawsScreen} options={{ title: 'Montana Laws' }} />
      <LawsStack.Screen name="LawsDetail" component={LawsDetailScreen} options={{ title: 'Law Details' }} />
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

export default function App() {
  return (
    <NavigationContainer>
      <StatusBar style="auto" />
      <Tab.Navigator
        screenOptions={({ route }) => ({
          tabBarIcon: ({ focused, color, size }) => {
            let iconName: keyof typeof Ionicons.glyphMap = 'home';

            if (route.name === 'Feed') {
              iconName = focused ? 'newspaper' : 'newspaper-outline';
            } else if (route.name === 'Jail Rosters') {
              iconName = focused ? 'list' : 'list-outline';
            } else if (route.name === 'Laws') {
              iconName = focused ? 'book' : 'book-outline';
            } else if (route.name === 'Blog') {
              iconName = focused ? 'create' : 'create-outline';
            }

            return <Ionicons name={iconName} size={size} color={color} />;
          },
          tabBarActiveTintColor: '#0f172a',
          tabBarInactiveTintColor: '#64748b',
          headerShown: false,
        })}
      >
        <Tab.Screen name="Feed" component={FeedStackNavigator} />
        <Tab.Screen name="Jail Rosters" component={JailRostersScreen} />
        <Tab.Screen name="Laws" component={LawsStackNavigator} />
        <Tab.Screen name="Blog" component={BlogStackNavigator} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
