import React, { ReactElement } from 'react';
import { render, RenderOptions } from '@testing-library/react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';

export const createTestQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  });

interface CustomRenderOptions extends Omit<RenderOptions, 'wrapper'> {
  route?: string;
  queryClient?: QueryClient;
}

function AllTheProviders({
  children,
  route = '/',
  queryClient = createTestQueryClient(),
}: {
  children: React.ReactNode;
  route?: string;
  queryClient?: QueryClient;
}) {
  // Navigate to the specified route
  React.useLayoutEffect(() => {
    window.history.pushState({}, 'Test page', route);
  }, [route]);

  return (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    </BrowserRouter>
  );
}

export const renderWithProviders = (
  ui: ReactElement,
  {
    route = '/',
    queryClient = createTestQueryClient(),
    ...renderOptions
  }: CustomRenderOptions = {}
) =>
  render(ui, {
    wrapper: ({ children }) => (
      <AllTheProviders route={route} queryClient={queryClient}>
        {children}
      </AllTheProviders>
    ),
    ...renderOptions,
  });

export * from '@testing-library/react';
