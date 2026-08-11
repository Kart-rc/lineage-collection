import { useId, useState } from "react";

import { ApiError } from "../../api/client";
import type {
  CollectionSourceType,
  RepositoryCollectionRequest,
} from "../../api/types";
import { readRuntimeConfig } from "../../config/runtime";


const EXACT_REVISION = /^[0-9a-f]{40}$/;

interface FormValues {
  sourceType: CollectionSourceType;
  origin: string;
  repository: string;
  revision: string;
  environment: string;
  platform: string;
  system: string;
  analyzerPack: string;
  ruleset: string;
  schemaProfile: string;
  checkoutPath: string;
}

const INITIAL: FormValues = {
  sourceType: "GIT",
  origin: "",
  repository: "",
  revision: "",
  environment: "staging",
  platform: "postgres",
  system: "",
  analyzerPack: "java-spring-data-jpa-v1",
  ruleset: "spring-data-rules-v1",
  schemaProfile: "postgres",
  checkoutPath: "",
};

export interface RepositoryCollectionFormProps {
  readonly onSubmit: (body: RepositoryCollectionRequest) => void;
  readonly pending: boolean;
  readonly error?: unknown;
  /** Injected in tests; defaults to the parsed runtime configuration. */
  readonly allowLocalCheckout?: boolean;
}


function validate(values: FormValues, allowLocalCheckout: boolean): string | null {
  if (values.sourceType === "LOCAL_CHECKOUT" && !allowLocalCheckout) {
    return "Local checkout collection is not available in this environment.";
  }
  if (!EXACT_REVISION.test(values.revision)) {
    return "Revision must be an exact lowercase 40-character commit.";
  }
  if (values.sourceType === "GIT" && !/^https:\/\/\S+$/.test(values.origin)) {
    return "Git origin must be a canonical HTTPS repository URL.";
  }
  if (values.sourceType === "LOCAL_CHECKOUT" && !values.checkoutPath.trim()) {
    return "A development checkout path is required for local collection.";
  }
  const required: (keyof FormValues)[] = [
    "origin",
    "repository",
    "environment",
    "platform",
    "system",
    "analyzerPack",
    "ruleset",
    "schemaProfile",
  ];
  const missing = required.find((field) => !values[field].trim());
  return missing ? "Every repository and analyzer field is required." : null;
}


function toRequest(values: FormValues): RepositoryCollectionRequest {
  const base = {
    sourceType: values.sourceType,
    origin: values.origin.trim(),
    repository: values.repository.trim(),
    revision: values.revision.trim(),
    environment: values.environment.trim(),
    platform: values.platform.trim(),
    system: values.system.trim(),
    analyzerPack: values.analyzerPack.trim(),
    ruleset: values.ruleset.trim(),
    schemaProfile: values.schemaProfile.trim(),
  };
  // A checkout path is never serialized for a GIT source, so a stale field value
  // in the form can never reach the wire.
  return values.sourceType === "LOCAL_CHECKOUT"
    ? { ...base, checkoutPath: values.checkoutPath.trim() }
    : base;
}


export function RepositoryCollectionForm({
  onSubmit,
  pending,
  error,
  allowLocalCheckout = readRuntimeConfig().demoActions,
}: RepositoryCollectionFormProps) {
  const id = useId();
  const [values, setValues] = useState<FormValues>(INITIAL);
  const [invalid, setInvalid] = useState<string | null>(null);

  const set = (field: keyof FormValues) => (
    event: { target: { value: string } },
  ) => setValues((current) => ({ ...current, [field]: event.target.value }));

  const field = (
    name: keyof FormValues,
    label: string,
    extra: { type?: string; placeholder?: string } = {},
  ) => (
    <p className="form-field">
      <label htmlFor={`${id}-${name}`}>{label}</label>
      <input
        id={`${id}-${name}`}
        name={name}
        type={extra.type ?? "text"}
        placeholder={extra.placeholder}
        value={values[name]}
        onChange={set(name)}
        disabled={pending}
      />
    </p>
  );

  const message =
    invalid ??
    (error
      ? error instanceof ApiError
        ? `${error.message} (${error.code} · ${error.correlationId})`
        : "The collection could not be submitted."
      : null);

  return (
    <form
      className="collection-form"
      aria-labelledby={`${id}-heading`}
      onSubmit={(event) => {
        event.preventDefault();
        if (pending) return;
        const problem = validate(values, allowLocalCheckout);
        setInvalid(problem);
        // Values are deliberately never reset, so a recoverable failure does not
        // discard what the operator typed.
        if (!problem) onSubmit(toRequest(values));
      }}
    >
      <h2 id={`${id}-heading`}>Collect a repository</h2>
      <p className="collection-form__lede">
        Submit an exact revision. Collection is durable: an equivalent request returns
        the same command, run and proposal.
      </p>

      <fieldset className="form-field" disabled={pending}>
        <legend>Source</legend>
        <label htmlFor={`${id}-sourceType`}>Source mode</label>
        <select
          id={`${id}-sourceType`}
          name="sourceType"
          value={values.sourceType}
          onChange={set("sourceType")}
        >
          <option value="GIT">Git repository URL</option>
          {/* Rendered only under the development policy — production omits the
              option entirely rather than disabling it. */}
          {allowLocalCheckout && (
            <option value="LOCAL_CHECKOUT">Local development checkout</option>
          )}
        </select>
      </fieldset>

      {field("origin", "Repository origin (HTTPS)", {
        placeholder: "https://github.com/spring-projects/spring-petclinic",
      })}
      {field("repository", "Repository name")}
      {field("revision", "Exact commit (40 hex)", {
        placeholder: "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
      })}
      {allowLocalCheckout && values.sourceType === "LOCAL_CHECKOUT"
        ? field("checkoutPath", "Development checkout path")
        : null}
      {field("environment", "Environment")}
      {field("system", "System")}
      {field("platform", "Platform")}
      {field("analyzerPack", "Analyzer pack")}
      {field("ruleset", "Ruleset")}
      {field("schemaProfile", "Schema profile")}

      <button className="button button--primary" type="submit" disabled={pending}>
        {pending ? "Submitting collection…" : "Collect repository"}
      </button>

      {message && (
        <p className="inline-error" role="alert">
          {message}
        </p>
      )}
    </form>
  );
}
