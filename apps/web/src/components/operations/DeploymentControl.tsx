import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "../../api/client";
import { Link } from "../../routing";


export interface DeploymentControlProps {
  /** Distinct systems seen in the run ledger, most recent first. */
  readonly systems: readonly string[];
  readonly environment: string;
}


function deploymentError(error: unknown): string {
  if (error instanceof ApiError) {
    if (/NO_PACKAGE/i.test(error.code)) {
      return "No published package for this system yet — approve a baseline first.";
    }
    return `${error.message} (${error.code} · ${error.correlationId})`;
  }
  return "The deployment pipeline could not be started.";
}


export function DeploymentControl({ systems, environment }: DeploymentControlProps) {
  const fieldId = useId();
  const queryClient = useQueryClient();
  // The ledger resolves after mount, so the default tracks props until the
  // user makes an explicit choice.
  const [chosen, setChosen] = useState<string | null>(null);
  const system = chosen ?? systems[0] ?? "";
  const deployment = useMutation({
    mutationFn: () => api.submitDeployment({ system, environment }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["overview"] }),
      ]);
    },
  });

  // The selected option must survive a ledger refresh reordering the list.
  const options = systems.includes(system) || !system ? systems : [system, ...systems];

  return (
    <form
      className="deployment-control"
      aria-label="Deployment pipeline control"
      onSubmit={(event) => {
        event.preventDefault();
        if (system) deployment.mutate();
      }}
    >
      <p className="eyebrow">Pipeline controls · deployment</p>
      <p className="deployment-control__lede">
        Promote the latest approved package for a system — runs D1–D6 against{" "}
        <code>{environment}</code> and advances the pointer fence.
      </p>
      <div className="deployment-control__row">
        <label htmlFor={`${fieldId}-system`}>System</label>
        <select
          id={`${fieldId}-system`}
          value={system}
          onChange={(event) => setChosen(event.target.value)}
          disabled={!options.length}
        >
          {options.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <button
          className="button button--primary deployment-control__submit"
          type="submit"
          disabled={!system || deployment.isPending}
        >
          {deployment.isPending ? "Starting pipeline…" : "Run deployment pipeline"}
        </button>
      </div>
      {deployment.data && (
        <div className="action-result" role="status">
          <strong>Deployment pipeline started</strong>
          <Link to={`/runs/${deployment.data.commandId}`}>
            <code>{deployment.data.commandId}</code>
          </Link>
        </div>
      )}
      {deployment.isError && (
        <p className="inline-error" role="alert">
          {deploymentError(deployment.error)}
        </p>
      )}
    </form>
  );
}
