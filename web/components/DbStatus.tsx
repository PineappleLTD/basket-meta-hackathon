"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/auth";

const POLL_MS = 15_000;

type Status = "checking" | "up" | "down";

const LABEL: Record<Status, string> = {
  checking: "Checking the API…",
  up: "API and database are reachable",
  down: "Can't reach the API or its database",
};

// Small light next to the logo, from GET /health (which pings the database).
export function DbStatus() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    const check = () =>
      fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(5_000) })
        .then((res) => setStatus(res.ok ? "up" : "down"))
        .catch(() => setStatus("down"));
    check();
    const id = setInterval(check, POLL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <span className={`db-status ${status}`} title={LABEL[status]} role="status" aria-label={LABEL[status]}>
      <i aria-hidden="true" />
      database
    </span>
  );
}
