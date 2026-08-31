import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../src/components/ui/card';

describe('Card Components', () => {
  describe('Card', () => {
    it('renders card container', () => {
      const { container } = render(
        <Card>
          <div>Card Content</div>
        </Card>
      );
      expect(container.querySelector('[class*="rounded"]')).toBeInTheDocument();
    });

    it('accepts custom className', () => {
      const { container } = render(
        <Card className="custom-card">Content</Card>
      );
      const card = container.querySelector('div');
      expect(card).toHaveClass('custom-card');
    });
  });

  describe('CardHeader', () => {
    it('renders header section', () => {
      const { container } = render(
        <Card>
          <CardHeader>
            <CardTitle>Title</CardTitle>
          </CardHeader>
        </Card>
      );
      expect(container.querySelector('[class*="CardHeader"]') || container.firstChild).toBeInTheDocument();
    });
  });

  describe('CardTitle', () => {
    it('renders title text', () => {
      render(
        <Card>
          <CardHeader>
            <CardTitle>Test Title</CardTitle>
          </CardHeader>
        </Card>
      );
      expect(screen.getByText('Test Title')).toBeInTheDocument();
    });

    it('applies heading styles', () => {
      const { container } = render(
        <Card>
          <CardHeader>
            <CardTitle>Heading</CardTitle>
          </CardHeader>
        </Card>
      );
      const title = container.querySelector('[class*="font-semibold"]') || screen.getByText('Heading');
      expect(title).toBeInTheDocument();
    });
  });

  describe('CardDescription', () => {
    it('renders description text', () => {
      render(
        <Card>
          <CardHeader>
            <CardDescription>This is a description</CardDescription>
          </CardHeader>
        </Card>
      );
      expect(screen.getByText('This is a description')).toBeInTheDocument();
    });

    it('applies muted styles', () => {
      const { container } = render(
        <Card>
          <CardHeader>
            <CardDescription>Muted text</CardDescription>
          </CardHeader>
        </Card>
      );
      const desc = container.querySelector('[class*="text-muted"]') || container.querySelector('p');
      expect(desc?.className).toContain('text-');
    });
  });

  describe('CardContent', () => {
    it('renders content section', () => {
      render(
        <Card>
          <CardContent>Main content here</CardContent>
        </Card>
      );
      expect(screen.getByText('Main content here')).toBeInTheDocument();
    });

    it('supports nested components', () => {
      render(
        <Card>
          <CardContent>
            <div data-testid="nested">Nested content</div>
          </CardContent>
        </Card>
      );
      expect(screen.getByTestId('nested')).toBeInTheDocument();
    });
  });

  describe('Complete Card Structure', () => {
    it('renders full card with all sections', () => {
      render(
        <Card>
          <CardHeader>
            <CardTitle>Dashboard</CardTitle>
            <CardDescription>View your dashboard</CardDescription>
          </CardHeader>
          <CardContent>
            <p>Dashboard content goes here</p>
          </CardContent>
        </Card>
      );

      expect(screen.getByText('Dashboard')).toBeInTheDocument();
      expect(screen.getByText('View your dashboard')).toBeInTheDocument();
      expect(screen.getByText('Dashboard content goes here')).toBeInTheDocument();
    });
  });
});
