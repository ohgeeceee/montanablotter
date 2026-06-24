import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import * as authApi from '../services/auth';
import { PublicUser } from '../services/auth';

interface AuthState {
  user: PublicUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (displayName: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<PublicUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function loadSession() {
      try {
        const token = await authApi.getToken();
        if (token && mounted) {
          const me = await authApi.fetchMe();
          if (mounted) setUser(me);
        }
      } catch {
        await authApi.removeToken();
      } finally {
        if (mounted) setIsLoading(false);
      }
    }
    loadSession();
    return () => {
      mounted = false;
    };
  }, []);

  const login = async (email: string, password: string) => {
    const { user: me, token } = await authApi.login(email, password);
    await authApi.setToken(token);
    setUser(me);
  };

  const register = async (displayName: string, email: string, password: string) => {
    const { user: me, token } = await authApi.register(displayName, email, password);
    await authApi.setToken(token);
    setUser(me);
  };

  const logout = async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
