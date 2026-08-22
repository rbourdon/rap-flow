import React from 'react';

interface JobProgressProps {
  status: string;
  stage: string | null;
}

const STAGES = [
  "Downloading Audio",
  "Separating Vocals",
  "Analyzing Syllables",
  "Synthesizing Beats",
  "Saving Results"
];

export function JobProgress({ status, stage }: JobProgressProps) {
  // Determine the current stage index.
  // If status is PENDING, index is 0.
  // If status is COMPLETED, all stages are done.
  // Otherwise, find the index of the current stage string.
  let currentIndex = -1;

  if (status === 'COMPLETED') {
    currentIndex = STAGES.length;
  } else if (status === 'PENDING') {
    currentIndex = -1; // Wait for the first stage
  } else if (status === 'PROCESSING') {
    if (stage) {
      currentIndex = STAGES.indexOf(stage);
    } else {
      currentIndex = 0;
    }
  } else if (status === 'FAILED') {
    if (stage) {
      currentIndex = STAGES.indexOf(stage);
    } else {
      currentIndex = 0; // Or whatever is appropriate for failed
    }
  }

  return (
    <div className="w-full max-w-md mx-auto py-8">
      <div className="relative">
        {/* The continuous vertical line behind nodes */}
        <div className="absolute left-4 top-4 bottom-4 w-0.5 bg-neutral-800" />

        <div className="flex flex-col space-y-6">
          {STAGES.map((s, idx) => {
            let state: 'waiting' | 'active' | 'completed' | 'failed' = 'waiting';

            if (status === 'FAILED' && idx === currentIndex) {
              state = 'failed';
            } else if (idx < currentIndex || status === 'COMPLETED') {
              state = 'completed';
            } else if (idx === currentIndex && status !== 'FAILED') {
              state = 'active';
            }

            return (
              <div key={s} className="relative flex items-center gap-6">
                <div className="relative z-10 flex items-center justify-center w-8 h-8 rounded-full shadow shrink-0">
                  {state === 'completed' && (
                    <div className="w-8 h-8 bg-green-500 rounded-full flex items-center justify-center text-white ring-4 ring-black">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  )}
                  {state === 'active' && (
                    <div className="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center ring-4 ring-black">
                      <span className="w-3 h-3 bg-white rounded-full animate-pulse" />
                    </div>
                  )}
                  {state === 'waiting' && (
                    <div className="w-8 h-8 bg-neutral-800 rounded-full ring-4 ring-black" />
                  )}
                  {state === 'failed' && (
                    <div className="w-8 h-8 bg-red-500 rounded-full flex items-center justify-center text-white ring-4 ring-black">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </div>
                  )}
                </div>
                <div className={`text-base font-medium transition-colors duration-300 ${
                  state === 'active' ? 'text-indigo-400' :
                  state === 'completed' ? 'text-neutral-300' :
                  state === 'failed' ? 'text-red-400' :
                  'text-neutral-600'
                }`}>
                  {s}
                  {state === 'active' && <span className="ml-2 inline-block animate-bounce">...</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
