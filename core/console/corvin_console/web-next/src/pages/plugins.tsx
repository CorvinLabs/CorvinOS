/**
 * Plugins Management Page
 * 
 * Browse, enable/disable, and configure installed plugins
 * Accessible from Settings → Plugins
 */

import React from 'react';
import Head from 'next/head';
import { useAuth } from '@/lib/auth';
import { PluginsPanel } from '@/components/PluginsPanel';

export default function PluginsPage() {
  const { user, isLoading, error: authError } = useAuth();

  if (isLoading) {
    return (
      <>
        <Head>
          <title>Plugins — CorvinOS</title>
        </Head>
        <div className="flex items-center justify-center min-h-screen">
          <div className="text-gray-600">Loading...</div>
        </div>
      </>
    );
  }

  if (authError || !user) {
    return (
      <>
        <Head>
          <title>Plugins — CorvinOS</title>
        </Head>
        <div className="flex items-center justify-center min-h-screen">
          <div className="text-red-600">Authentication required</div>
        </div>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>Plugins — CorvinOS</title>
        <meta name="description" content="Manage installed plugins" />
      </Head>

      <main className="container mx-auto py-8 px-4">
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">Plugins</h1>
          <p className="text-gray-600">
            Install, enable, disable, and configure plugins for your CorvinOS instance
          </p>
        </div>

        <PluginsPanel />
      </main>
    </>
  );
}
