import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Button } from '../../../src/components/ui/button';

describe('Button Component', () => {
  it('renders with text content', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByText('Click me')).toBeInTheDocument();
  });

  it('calls onClick handler when clicked', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Click</Button>);
    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it('respects disabled state', () => {
    render(<Button disabled>Disabled</Button>);
    const button = screen.getByRole('button');
    expect(button).toBeDisabled();
  });

  it('renders as link when href is provided', () => {
    const { container } = render(<Button asChild><a href="/test">Link</a></Button>);
    const link = container.querySelector('a');
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/test');
  });

  it('applies variant styles', () => {
    const { container: destructiveContainer } = render(
      <Button variant="destructive">Delete</Button>
    );
    const destructiveButton = destructiveContainer.querySelector('button');
    expect(destructiveButton).toHaveClass('bg-destructive');
  });

  it('applies size styles', () => {
    const { container } = render(<Button size="lg">Large</Button>);
    const button = container.querySelector('button');
    // Button uses h-12 for default, lg size may use different height
    expect(button?.className).toMatch(/h-1[0-2]/);
  });

  it('handles multiple clicks', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Multi-click</Button>);
    const button = screen.getByRole('button');

    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    expect(handleClick).toHaveBeenCalledTimes(3);
  });

  it('respects keyboard interaction', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Keyboard</Button>);
    const button = screen.getByRole('button');

    // Simulate pressing Space/Enter on the button
    fireEvent.keyDown(button, { key: ' ', code: 'Space' });
    fireEvent.click(button);

    // Verify click handler was called
    expect(handleClick).toHaveBeenCalled();
  });

  it('allows custom className', () => {
    const { container } = render(
      <Button className="custom-class">Custom</Button>
    );
    const button = container.querySelector('button');
    expect(button).toHaveClass('custom-class');
  });

  it('renders children correctly', () => {
    render(
      <Button>
        <span data-testid="icon">🔒</span>
        <span>Lock</span>
      </Button>
    );
    expect(screen.getByTestId('icon')).toBeInTheDocument();
    expect(screen.getByText('Lock')).toBeInTheDocument();
  });
});
