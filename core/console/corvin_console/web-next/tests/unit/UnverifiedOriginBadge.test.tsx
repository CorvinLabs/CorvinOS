import { render, screen } from '@testing-library/react';
import { UnverifiedOriginBadge } from '@/components/forge/UnverifiedOriginBadge';

describe('UnverifiedOriginBadge', () => {
  it('renders the badge with correct text', () => {
    render(<UnverifiedOriginBadge />);
    expect(screen.getByText('Unverified origin')).toBeInTheDocument();
  });

  it('contains the alert icon', () => {
    const { container } = render(<UnverifiedOriginBadge />);
    const svg = container.querySelector('svg');
    expect(svg).toBeInTheDocument();
  });
});
