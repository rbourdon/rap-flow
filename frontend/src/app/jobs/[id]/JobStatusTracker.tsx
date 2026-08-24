'use client'

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { JobProgress } from '@/components/JobProgress';

interface StageState {
  state?: string;
  reused?: boolean;
  updatedAt?: string;
}

interface JobStatusTrackerProps {
  jobId: string;
  initialStatus: string;
  initialStage: string | null;
  createdAt: Date | string;
  initialStageStates?: Record<string, StageState> | null;
}

export function JobStatusTracker({
  jobId,
  initialStatus,
  initialStage,
  createdAt,
  initialStageStates = null,
}: JobStatusTrackerProps) {
  const [status, setStatus] = useState(initialStatus);
  const [stage, setStage] = useState(initialStage);
  const [stageStates, setStageStates] = useState<Record<string, StageState> | null>(initialStageStates);
  const router = useRouter();

  useEffect(() => {
    // Stop polling if the job is finished
    if (status === 'COMPLETED' || status === 'FAILED') return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}/status`);
        if (res.ok) {
          const data = await res.json();
          setStatus(data.status);
          setStage(data.stage);
          if (data.stageStates) setStageStates(data.stageStates);

          // If the status changed to terminal, refresh the page to show results
          if (data.status === 'COMPLETED' || data.status === 'FAILED') {
            router.refresh();
          }
        }
      } catch (err) {
        console.error('Failed to poll status', err);
      }
    }, 2500); // Check every 2.5s

    return () => clearInterval(interval);
  }, [jobId, status, router]);

  return (
    <div className="mt-8 bg-white/[0.02] border border-white/5 rounded-3xl p-8 shadow-2xl backdrop-blur-sm">
      <div className="flex items-center justify-between gap-3 mb-2">
        <h2 className="text-xl font-semibold">Processing Pipeline</h2>
        <span className="inline-flex items-center gap-2 text-xs text-neutral-400">
          <span className="h-2 w-2 rounded-full bg-indigo-400 motion-safe:animate-pulse" />
          Live — this page updates automatically
        </span>
      </div>
      <JobProgress status={status} stage={stage} createdAt={createdAt} stageStates={stageStates} />
    </div>
  );
}
