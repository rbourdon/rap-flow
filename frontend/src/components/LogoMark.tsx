// The rap-flow brand mark.
//
// One continuous stroke that starts as a smooth vocal waveform and resolves
// into a square, grid-locked pulse — the product in a single gesture: the flow
// goes in analog, comes out quantized to the song's grid as percussion.
//
// The glyph is kept in sync with `src/app/icon.svg` (browser tab / favicon);
// change one and change the other.
//
// Purely decorative: every place it renders sits next to the "rap-flow"
// wordmark, so it is hidden from assistive tech rather than labelled twice.
export function LogoMark({ className = '' }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`relative inline-flex shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-cyan-400 shadow-lg shadow-indigo-500/25 ring-1 ring-white/20 ring-inset ${className}`}
    >
      <svg
        viewBox="0 0 24 24"
        className="w-[78%] h-[78%]"
        fill="none"
        stroke="white"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* vocal flow → quantized pulse, drawn as one unbroken path */}
        <path d="M2.6 12c1.05-4.4 3.15-4.4 4.2 0s3.15 4.4 4.2 0V8.1h3.6v7.8h3.6V12h3.2" />
      </svg>
    </span>
  )
}
