/**
 * CorvinOS Onboarding Module
 *
 * This module exports onboarding and help components for the CorvinOS console.
 *
 * Components:
 * - OnboardingWizard: Interactive 5-step onboarding for new operators
 * - HelpMenu: Comprehensive documentation and resource browser
 *
 * Usage:
 *
 *   import { OnboardingWizard, HelpMenu } from '@/core/console/onboarding';
 *
 *   function MyApp() {
 *     return (
 *       <>
 *         <OnboardingWizard />
 *         <HelpMenu />
 *       </>
 *     );
 *   }
 */

export { default as OnboardingWizard } from './OnboardingWizard';
export { default as HelpMenu } from './HelpMenu';

/**
 * Hook to check if onboarding is complete
 */
export const useOnboardingStatus = () => {
  const [isComplete, setIsComplete] = React.useState(false);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    const checkStatus = async () => {
      try {
        const response = await fetch('/v1/console/onboarding/status', {
          headers: {
            'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`,
          },
        });
        const data = await response.json();
        setIsComplete(data.status === 'complete');
      } catch (error) {
        console.error('Failed to check onboarding status:', error);
      } finally {
        setLoading(false);
      }
    };

    checkStatus();
  }, []);

  return { isComplete, loading };
};

/**
 * Hook to mark onboarding as complete
 */
export const useCompleteOnboarding = () => {
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<Error | null>(null);

  const complete = async (tenantId = '_default') => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/v1/console/onboarding/complete', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`,
        },
        body: JSON.stringify({ tenant_id: tenantId }),
      });

      if (!response.ok) {
        throw new Error('Failed to mark onboarding as complete');
      }

      return await response.json();
    } catch (err) {
      const error = err instanceof Error ? err : new Error('Unknown error');
      setError(error);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  return { complete, loading, error };
};

/**
 * Hook to fetch onboarding resources
 */
export const useOnboardingResources = () => {
  const [resources, setResources] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<Error | null>(null);

  React.useEffect(() => {
    const fetchResources = async () => {
      try {
        const response = await fetch('/v1/console/onboarding/resources', {
          headers: {
            'Authorization': `Bearer ${process.env.REACT_APP_API_KEY}`,
          },
        });
        const data = await response.json();
        setResources(data);
      } catch (err) {
        setError(err instanceof Error ? err : new Error('Unknown error'));
      } finally {
        setLoading(false);
      }
    };

    fetchResources();
  }, []);

  return { resources, loading, error };
};

/**
 * Onboarding context for sharing state across components
 */
const OnboardingContext = React.createContext<{
  isComplete: boolean;
  currentStep: number;
  setCurrentStep: (step: number) => void;
  markComplete: () => Promise<void>;
} | null>(null);

export const OnboardingProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isComplete: isCompleteInitial } = useOnboardingStatus();
  const { complete: markComplete } = useCompleteOnboarding();
  const [isComplete, setIsComplete] = React.useState(isCompleteInitial);
  const [currentStep, setCurrentStep] = React.useState(0);

  const handleMarkComplete = async () => {
    try {
      await markComplete();
      setIsComplete(true);
    } catch (error) {
      console.error('Failed to mark onboarding as complete:', error);
      throw error;
    }
  };

  return (
    <OnboardingContext.Provider
      value={{
        isComplete,
        currentStep,
        setCurrentStep,
        markComplete: handleMarkComplete,
      }}
    >
      {children}
    </OnboardingContext.Provider>
  );
};

/**
 * Hook to use onboarding context
 */
export const useOnboarding = () => {
  const context = React.useContext(OnboardingContext);
  if (!context) {
    throw new Error('useOnboarding must be used within OnboardingProvider');
  }
  return context;
};

// Type exports
export type { HelpResource, HelpMenuState } from './HelpMenu';
export type { OnboardingStep, OnboardingState } from './OnboardingWizard';
