// Shared design-system class constants. One button radius (rounded-lg), one
// card style, and a consistent indigo focus-visible ring on the dark bg. Import
// these instead of re-deriving button/card classes per component.

// Applied to every interactive control for a consistent keyboard focus ring.
export const focusRing =
  'focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 focus-visible:ring-offset-black'

const buttonBase =
  `inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${focusRing}`

// Primary action (indigo).
export const btnPrimary =
  `${buttonBase} bg-indigo-600 hover:bg-indigo-500 text-white`

// Secondary action (subtle white/5 with border).
export const btnSecondary =
  `${buttonBase} bg-white/5 hover:bg-white/10 border border-white/10 text-white/80 hover:text-white`

// Destructive action (red).
export const btnDestructive =
  `${buttonBase} bg-red-600/90 hover:bg-red-600 text-white`

// Standard glass card.
export const card =
  'bg-white/[0.02] border border-white/5 rounded-3xl shadow-2xl backdrop-blur-sm'
