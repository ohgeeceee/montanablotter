/** Tailwind config for Montana Blotter.
 *  Scans all templates and static JS for class names so the generated
 *  static/tailwind.generated.css contains exactly the utilities the site uses.
 *  This removes the runtime dependency on cdn.tailwindcss.com.
 */
module.exports = {
  content: [
    './templates/**/*.html',
    './static/**/*.js',
    './static/**/*.jsx',
  ],
  // The site uses a handful of custom colors via Tailwind's color utilities
  // (e.g. text-slate-900, bg-amber-50). Those are part of Tailwind's default
  // palette, so no theme extension is required. If brand colors are added
  // later, extend `theme.extend.colors` here.
  theme: {
    extend: {},
  },
  plugins: [],
  // Don't purge the safelist; the content scan handles it.
};
