import type { ReactNode } from "react";

/**
 * Render the tiny inline-markup subset the backend uses in setup-guide steps
 * (`**bold**` and `` `code` ``) as React elements.
 *
 * This replaces two `dangerouslySetInnerHTML` sites (SetupGate, bridges) that
 * ran a regex→HTML rewrite over server text and injected the result unescaped:
 * any `<`/`&` inside a step — or a step body sourced from an operator-editable
 * guide — landed in the DOM as markup. React escapes text children by
 * construction, so the only elements that can appear here are the ones this
 * function creates.
 */
const TOKEN = /(\*\*.+?\*\*|`.+?`)/g;

export function renderInlineMarkup(
  text: string,
  codeClassName = "font-mono bg-muted px-1 rounded text-[10px]",
): ReactNode[] {
  return text.split(TOKEN).map((part, i) => {
    if (part.length >= 4 && part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.length >= 2 && part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={i} className={codeClassName}>
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}
