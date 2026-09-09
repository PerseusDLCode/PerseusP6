# Design Decisions, or, Maintenance and Complexity

Minimum Viable Perseus (MVP) aims to replicate as much of the functionality of Hopper (P4)
as possible in a minimal computing environment. This decision requires some tradeoffs as
well as some careful maintenance. This document serves as a living record of the rationale
behind these decisions, and thus as a modest attempt to justify them for the sake of
relatively straightforward maintainability.


## The meaning of "minimal"

"Minimal computing" has a range of meanings, not all of which apply to MVP. In our case,
we use minimal computing to mean 1) a statically compiled site and 2) no backend database
for core functionality. In practice, these decisions mean that search functionality, for
example, needs to come from a pre-built index that the client can download and cache from
the browser. These requirements limit the search index to a JSON format and the search
implementation to a vendored JavaScript library, Fuse.js.

## Architecture

Static compilation also encourages us to be as declarative as possible when it comes to
rendering, allowing the TEI XML files to dictate their needs by convention rather than
heavy customization by the consumer (MVP). The `ReadableTextContainer` component provides
a good example of this approach: it takes the tagname and attributes of any TEI XML element
and routes them to an implementation matching the tagname; if no implementation matches,
the element is rendered as a `<span>`. Any conditional rendering depends entirely on
the matched template, which enforces TEI conventions by refusing to render unusual
attributes or element configurations.

## Design and styles

Styles are handled by [DaisyUI](https://daisyui.com), a thin wrapper around
[Tailwind](https://tailwindcss.com). We use Tailwind's pre-compiled binary; developers
need to download it from Tailwind's distributions (or by using their preferred package
manager). The binary ensures that we only ship CSS that we actually use, keeping the payload
to the client as small as possible.

DaisyUI exposes a collection of variables through which we can customize the theme of the site.
We deliberately avoid building our own style system, limiting ourselves to the variables
that DaisyUI provides.

```css
@plugin "./daisyui-theme.mjs" {
    name: "perseus";
    default: true;
    prefersdark: false;
    color-scheme: light;

    /* Base: warm parchment/cream, echoing the Hopper's #f1f2ea boxes,
       lightened and neutralized for a cleaner 2026 reading surface. */
    --color-base-100: oklch(98% 0.006 80);
    --color-base-200: oklch(94% 0.012 75);
    --color-base-300: oklch(88% 0.018 70);
    --color-base-content: oklch(24% 0.01 60);

    /* Primary: the banner's terracotta/rust (#C56352), Perseus's signature
       red-orange, kept as the dominant brand color. */
    --color-primary: oklch(58% 0.13 35);
    --color-primary-content: oklch(98% 0.006 80);

    /* Secondary: the olive-grey box border (#d1d2ba) deepened into a usable
       olive accent for secondary actions. */
    --color-secondary: oklch(56% 0.05 115);
    --color-secondary-content: oklch(98% 0.006 80);

    /* Accent: the search-match highlight (#9999ff), a cool violet-blue
       counterpoint to the warm base/primary. */
    --color-accent: oklch(62% 0.13 280);
    --color-accent-content: oklch(98% 0.006 80);

    /* Neutral: the tab bar's warm charcoal grays (#888/#AAA). */
    --color-neutral: oklch(32% 0.012 60);
    --color-neutral-content: oklch(94% 0.012 75);

    --color-info: oklch(62% 0.11 235);
    --color-info-content: oklch(98% 0 0);
    --color-success: oklch(58% 0.11 145);
    --color-success-content: oklch(98% 0 0);
    --color-warning: oklch(78% 0.15 80);
    --color-warning-content: oklch(24% 0.01 60);
    --color-error: oklch(56% 0.18 25);
    --color-error-content: oklch(98% 0 0);

    --radius-selector: 0.5rem;
    --radius-field: 0.375rem;
    --radius-box: 0.75rem;
    --size-selector: 0.25rem;
    --size-field: 0.25rem;
    --border: 1px;
    --depth: 1;
    --noise: 0;
}
```
