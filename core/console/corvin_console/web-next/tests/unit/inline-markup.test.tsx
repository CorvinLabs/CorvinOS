/**
 * renderInlineMarkup — the React replacement for the regex→dangerouslySetInnerHTML
 * step renderer (SetupGate / bridges). The load-bearing property is that text
 * OUTSIDE the two markup tokens can never become markup.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { renderInlineMarkup } from '@/lib/inline-markup';

describe('renderInlineMarkup', () => {
  it('renders **bold** and `code` as elements', () => {
    const { container } = render(<span>{renderInlineMarkup('Run **corvin** with `--flag` now')}</span>);
    expect(container.querySelector('strong')?.textContent).toBe('corvin');
    expect(container.querySelector('code')?.textContent).toBe('--flag');
    expect(container.textContent).toBe('Run corvin with --flag now');
  });

  it('never turns HTML in the step text into markup (XSS regression)', () => {
    const hostile = 'step <img src=x onerror=alert(1)> and **<b>bold</b>** and `<script>x</script>`';
    const { container } = render(<span>{renderInlineMarkup(hostile)}</span>);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('strong')?.textContent).toBe('<b>bold</b>');
    expect(container.querySelector('code')?.textContent).toBe('<script>x</script>');
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('applies the caller-supplied code class', () => {
    const { container } = render(<span>{renderInlineMarkup('`x`', 'mono-x')}</span>);
    expect(container.querySelector('code')?.className).toBe('mono-x');
  });
});
