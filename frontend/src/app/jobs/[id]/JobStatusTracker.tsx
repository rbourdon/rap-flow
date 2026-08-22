'use client'

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { JobProgress } from '@/components/JobProgress';

interface JobStatusTrackerProps {
  jobId: string;
  initialStatus: string;
  initialStage: string | null;
}

export function JobStatusTracker({ jobId, initialStatus, initialStage }: JobStatusTrackerProps) {
  const [status, setStatus] = useState(initialStatus);
  const [stage, setStage] = useState(initialStage);
  const router = useRouter();

  useEffect(() => {
    // Stop polling if the job is finished
    if (status === 'COMPLETED' || status === 'FAILED') return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}/status`);
        if (res.ok) {
          const data = await res.json();
          if (data.status !== status || data.stage !== stage) {
            setStatus(data.status);
            setStage(data.stage);

            // If the status changed to terminal, we want the whole page to refresh to show results
            if (data.status === 'COMPLETED' || data.status === 'FAILED') {
              router.refresh();
            }
          }
        }
      } catch (err) {
        console.error('Failed to poll status', err);
      }
    }, 2500); // Check every 2.5s

    return () => clearInterval(interval);
  }, [jobId, status, stage, router]);

  return (
    <div className="mt-8 bg-white/[0.02] border border-white/5 rounded-3xl p-8 shadow-2xl backdrop-blur-sm">
      <h2 className="text-xl font-semibold mb-6">Processing Pipeline</h2>
      <JobProgress status={status} stage={stage} />
    </div>
  );
}
