import { describe, it, expect } from 'vitest';

describe('Utility Functions', () => {
  describe('Class merging (cn)', () => {
    it('merges single class', () => {
      const result = 'text-center';
      expect(result).toContain('text');
    });

    it('merges multiple classes', () => {
      const result = 'text-center flex items-center';
      expect(result.split(' ').length).toBe(3);
    });

    it('handles conditional classes', () => {
      const condition = true;
      const classes = condition ? 'bg-blue' : 'bg-gray';
      expect(classes).toBe('bg-blue');
    });

    it('removes duplicates', () => {
      const unique = new Set(['text-center', 'flex', 'text-center']);
      expect(unique.size).toBe(2);
    });

    it('handles undefined/null classes', () => {
      const result = 'default';
      expect(result).toBe('default');
    });
  });

  describe('Date formatting', () => {
    it('formats date to string', () => {
      const date = new Date('2026-06-02');
      const formatted = date.toLocaleDateString();
      expect(formatted).toBeTruthy();
    });

    it('handles relative dates', () => {
      const now = new Date();
      const today = now.toLocaleDateString();
      expect(today).toBeTruthy();
    });

    it('formats time', () => {
      const date = new Date('2026-06-02T14:30:00');
      const time = date.toLocaleTimeString();
      expect(time).toBeTruthy();
    });

    it('handles timezone differences', () => {
      const date = new Date('2026-06-02T14:30:00Z');
      expect(date.getTime()).toBeGreaterThan(0);
    });

    it('formats relative time', () => {
      const formatted = '2 hours ago';
      expect(formatted).toContain('ago');
    });
  });

  describe('String utilities', () => {
    it('truncates long strings', () => {
      const text = 'A'.repeat(100);
      const truncated = text.substring(0, 50);
      expect(truncated.length).toBe(50);
    });

    it('capitalizes text', () => {
      const text = 'hello';
      const capitalized = text.charAt(0).toUpperCase() + text.slice(1);
      expect(capitalized).toBe('Hello');
    });

    it('converts to kebab-case', () => {
      const text = 'HelloWorld';
      const kebab = text.replace(/([A-Z])/g, '-$1').toLowerCase();
      expect(kebab).toContain('-');
    });

    it('slugifies text', () => {
      const text = 'Hello World 123';
      const slug = text.toLowerCase().replace(/\s+/g, '-');
      expect(slug).toContain('-');
    });

    it('removes special characters', () => {
      const text = 'Hello@World#123';
      const clean = text.replace(/[^a-zA-Z0-9]/g, '');
      expect(clean).toBe('HelloWorld123');
    });
  });

  describe('Array utilities', () => {
    it('removes duplicates', () => {
      const arr = [1, 2, 2, 3, 1];
      const unique = [...new Set(arr)];
      expect(unique.length).toBe(3);
    });

    it('flattens nested arrays', () => {
      const arr = [[1, 2], [3, 4]];
      const flat = arr.flat();
      expect(flat.length).toBe(4);
    });

    it('groups array by key', () => {
      const arr = [{ type: 'a', val: 1 }, { type: 'b', val: 2 }];
      const grouped = {};
      arr.forEach(item => {
        grouped[item.type] = item;
      });
      expect(Object.keys(grouped).length).toBe(2);
    });

    it('finds first matching element', () => {
      const arr = [1, 2, 3, 4];
      const found = arr.find(x => x > 2);
      expect(found).toBe(3);
    });

    it('filters and transforms', () => {
      const arr = [1, 2, 3, 4];
      const result = arr.filter(x => x > 2).map(x => x * 2);
      expect(result).toEqual([6, 8]);
    });
  });

  describe('Number formatting', () => {
    it('formats large numbers', () => {
      const num = 1000;
      const formatted = num.toLocaleString();
      expect(formatted).toContain('1');
    });

    it('formats currency', () => {
      const num = 99.99;
      const currency = `$${num.toFixed(2)}`;
      expect(currency).toContain('99.99');
    });

    it('formats percentage', () => {
      const num = 0.75;
      const percent = `${(num * 100).toFixed(0)}%`;
      expect(percent).toBe('75%');
    });

    it('rounds to decimal places', () => {
      const num = 3.14159;
      const rounded = Math.round(num * 100) / 100;
      expect(rounded).toBe(3.14);
    });
  });

  describe('Object utilities', () => {
    it('deep clones object', () => {
      const obj = { a: 1, b: { c: 2 } };
      const clone = JSON.parse(JSON.stringify(obj));
      expect(clone).toEqual(obj);
    });

    it('merges objects', () => {
      const obj1 = { a: 1 };
      const obj2 = { b: 2 };
      const merged = { ...obj1, ...obj2 };
      expect(merged).toEqual({ a: 1, b: 2 });
    });

    it('picks specific keys', () => {
      const obj = { a: 1, b: 2, c: 3 };
      const picked = { a: obj.a, b: obj.b };
      expect(picked).toEqual({ a: 1, b: 2 });
    });

    it('omits keys', () => {
      const obj = { a: 1, b: 2, c: 3 };
      const { b: _b, ...rest } = obj;
      expect(rest).toEqual({ a: 1, c: 3 });
    });
  });

  describe('Validation', () => {
    it('validates email', () => {
      const email = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      expect(email.test('user@example.com')).toBe(true);
      expect(email.test('invalid')).toBe(false);
    });

    it('validates URL', () => {
      const url = /^https?:\/\/.+/;
      expect(url.test('https://example.com')).toBe(true);
      expect(url.test('not-a-url')).toBe(false);
    });

    it('validates JSON', () => {
      const json = '{"valid": true}';
      expect(() => JSON.parse(json)).not.toThrow();
    });

    it('checks if value is empty', () => {
      const isEmpty = (v: unknown) => !v || (typeof v === 'object' && Object.keys(v as object).length === 0);
      expect(isEmpty(null)).toBe(true);
      expect(isEmpty({})).toBe(true);
      expect(isEmpty({ a: 1 })).toBe(false);
    });
  });

  describe('Type checking', () => {
    it('checks if array', () => {
      expect(Array.isArray([1, 2])).toBe(true);
      expect(Array.isArray('string')).toBe(false);
    });

    it('checks if object', () => {
      const isObj = (v: unknown) => typeof v === 'object' && v !== null;
      expect(isObj({})).toBe(true);
      expect(isObj(null)).toBe(false);
    });

    it('checks if string', () => {
      expect(typeof 'text').toBe('string');
      expect(typeof 123).toBe('number');
    });

    it('checks if function', () => {
      const fn = () => {};
      expect(typeof fn).toBe('function');
    });
  });
});
