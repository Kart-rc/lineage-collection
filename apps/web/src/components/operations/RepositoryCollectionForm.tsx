import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ApiError } from "../../api/client";
import type {
  CollectionSourceType,
  RepositoryCollectionRequest,
} from "../../api/types";
import { repositoryOriginProblem } from "../../api/repositoryOrigin";
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

/**
 * Client mirror of the server's analyzer registry
 * (`AnalyzerRegistry.default()` in apps/api). The pack pre-selected in the
 * wizard is the java-spring default — it is a default, not an inspection
 * result, and the UI says so.
 */
interface AnalyzerPackOption {
  readonly pack: string;
  readonly ruleset: string;
  readonly tile: string;
  readonly summary: string;
  readonly schemaProfiles: readonly string[];
}

const ANALYZER_PACKS: readonly AnalyzerPackOption[] = [
  {
    pack: "java-spring-data-jpa-v1",
    ruleset: "spring-data-rules-v1",
    tile: "JPA",
    summary: "Spring Data repositories, JPA entities, JDBC templates",
    schemaProfiles: ["h2", "mysql", "postgres"],
  },
  {
    pack: "kafka-streams-v1",
    ruleset: "kafka-binding-rules-v1",
    tile: "KFK",
    summary: "Spring Cloud Stream bindings and Kafka topics",
    schemaProfiles: ["kafka"],
  },
  {
    pack: "sql-transformation-v1",
    ruleset: "sql-transformation-rules-v1",
    tile: "SQL",
    summary: "SQL transformation scripts resolved against the catalog",
    schemaProfiles: ["postgres", "snowflake"],
  },
  {
    pack: "python-fixture-v1",
    ruleset: "python-demo-v1",
    tile: "PY",
    summary: "Python dataset-API fixture analyzer",
    schemaProfiles: ["snowflake"],
  },
];

const RULESETS = ANALYZER_PACKS.map((definition) => definition.ruleset);
const ENVIRONMENTS = ["development", "staging", "production"] as const;

const STEPS = [
  { title: "Source", detail: "Origin, revision & system" },
  { title: "Analysis profile", detail: "Analyzer pack & schema" },
  { title: "Review & collect", detail: "Confirm and submit" },
] as const;

export interface RepositoryCollectionFormProps {
  readonly onSubmit: (body: RepositoryCollectionRequest) => void;
  readonly pending: boolean;
  readonly error?: unknown;
  /** Injected in tests; defaults to the parsed runtime configuration. */
  readonly allowLocalCheckout?: boolean;
  /** Post-submit tracking panel; when present the wizard shows its tracking state. */
  readonly tracking?: ReactNode;
  /** Stable id for the tracking panel so "back to the form" survives resubmission. */
  readonly trackingId?: string;
}


function packDefinition(pack: string): AnalyzerPackOption {
  return (
    ANALYZER_PACKS.find((definition) => definition.pack === pack) ??
    ANALYZER_PACKS[0]
  );
}


function validateSource(
  values: FormValues,
  allowLocalCheckout: boolean,
): string | null {
  if (values.sourceType === "LOCAL_CHECKOUT" && !allowLocalCheckout) {
    return "Local checkout collection is not available in this environment.";
  }
  if (!EXACT_REVISION.test(values.revision)) {
    return "Revision must be an exact lowercase 40-character commit.";
  }
  // Mirror the server's canonical-origin rules so the form cannot accept an origin
  // the API would refuse with INVALID_REQUEST.
  const originProblem = repositoryOriginProblem(
    values.origin.trim(),
    values.repository.trim(),
  );
  if (originProblem) return originProblem;
  if (values.sourceType === "LOCAL_CHECKOUT" && !values.checkoutPath.trim()) {
    return "A development checkout path is required for local collection.";
  }
  if (!values.system.trim()) {
    return "A system name is required — it names the graph namespace.";
  }
  return null;
}


function validate(values: FormValues, allowLocalCheckout: boolean): string | null {
  const sourceProblem = validateSource(values, allowLocalCheckout);
  if (sourceProblem) return sourceProblem;
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
  tracking,
  trackingId,
}: RepositoryCollectionFormProps) {
  const id = useId();
  const [values, setValues] = useState<FormValues>(INITIAL);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [packPickerOpen, setPackPickerOpen] = useState(false);
  const [dismissedTracking, setDismissedTracking] = useState<string | null>(null);

  const trackingKey = tracking != null ? trackingId ?? "tracking" : null;
  const showTracking = trackingKey !== null && dismissedTracking !== trackingKey;
  const view = showTracking ? "tracking" : `step-${step}`;

  // Move focus to the step heading whenever the wizard changes panel, but never
  // on first mount.
  const titleRef = useRef<HTMLHeadingElement>(null);
  const viewRef = useRef(view);
  useEffect(() => {
    if (viewRef.current !== view) {
      viewRef.current = view;
      titleRef.current?.focus();
    }
  }, [view]);

  const set = (field: keyof FormValues) => (
    event: { target: { value: string } },
  ) => setValues((current) => ({ ...current, [field]: event.target.value }));

  // Selecting a pack keeps the plan coherent with the server's registry: the
  // ruleset follows the pack, and the schema profile / platform are pulled back
  // into the pack's supported set when the current choice falls outside it.
  const choosePack = (pack: string) => {
    setValues((current) => {
      const definition = packDefinition(pack);
      const schemaSupported = definition.schemaProfiles.includes(
        current.schemaProfile,
      );
      const schemaProfile = schemaSupported
        ? current.schemaProfile
        : definition.schemaProfiles[definition.schemaProfiles.length - 1];
      return {
        ...current,
        analyzerPack: definition.pack,
        ruleset: definition.ruleset,
        schemaProfile,
        platform: schemaSupported ? current.platform : schemaProfile,
      };
    });
  };

  const advance = () => {
    const problem = step === 0 ? validateSource(values, allowLocalCheckout) : null;
    setInvalid(problem);
    if (!problem) setStep((current) => Math.min(current + 1, STEPS.length - 1));
  };

  const goTo = (target: number) => {
    if (pending) return;
    if (!showTracking && target > step) return;
    if (showTracking && trackingKey) setDismissedTracking(trackingKey);
    setInvalid(null);
    setStep(target);
  };

  const message =
    invalid ??
    (error
      ? error instanceof ApiError
        ? `${error.message} (${error.code} · ${error.correlationId})`
        : "The collection could not be submitted."
      : null);

  const activePack = packDefinition(values.analyzerPack);
  const packIsDefault = values.analyzerPack === INITIAL.analyzerPack;
  const repositoryTyped = values.repository.trim();
  const revisionTyped = values.revision.trim();

  const textField = (
    name: keyof FormValues,
    label: string,
    extra: { placeholder?: string; mono?: boolean } = {},
  ) => (
    <p className={`wizard-field${extra.mono ? " wizard-field--mono" : ""}`}>
      <label htmlFor={`${id}-${name}`}>{label}</label>
      <input
        id={`${id}-${name}`}
        name={name}
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder={extra.placeholder}
        value={values[name]}
        onChange={set(name)}
      />
    </p>
  );

  const selectField = (
    name: keyof FormValues,
    label: string,
    options: readonly string[],
    mono = false,
  ) => (
    <p className={`wizard-field${mono ? " wizard-field--mono" : ""}`}>
      <label htmlFor={`${id}-${name}`}>{label}</label>
      <select
        id={`${id}-${name}`}
        name={name}
        value={values[name]}
        onChange={set(name)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </p>
  );

  const planRows: readonly { key: string; value: string }[] = [
    { key: "Source", value: values.sourceType },
    { key: "Repository", value: repositoryTyped },
    { key: "Revision", value: revisionTyped ? revisionTyped.slice(0, 12) : "" },
    { key: "System", value: values.system.trim() },
    { key: "Environment", value: values.environment },
    { key: "Platform", value: values.platform },
    { key: "Analyzer pack", value: values.analyzerPack },
    { key: "Ruleset", value: values.ruleset },
    { key: "Schema profile", value: values.schemaProfile },
  ];

  const reviewRows: readonly { key: string; value: string }[] = [
    {
      key: "Source mode",
      value:
        values.sourceType === "GIT" ? "GIT · repository URL" : "LOCAL_CHECKOUT",
    },
    { key: "Origin", value: values.origin.trim() },
    { key: "Repository", value: repositoryTyped },
    { key: "Revision", value: revisionTyped },
    ...(values.sourceType === "LOCAL_CHECKOUT"
      ? [{ key: "Checkout path", value: values.checkoutPath.trim() }]
      : []),
    { key: "System", value: values.system.trim() },
    { key: "Environment", value: values.environment },
    { key: "Platform", value: values.platform },
    { key: "Analyzer pack", value: values.analyzerPack },
    { key: "Ruleset", value: values.ruleset },
    { key: "Schema profile", value: values.schemaProfile },
    { key: "Element lineage", value: "derived where the analyzer can prove it" },
  ];

  const stepHead = (eyebrow: string, title: string, intro: ReactNode) => (
    <header className="wizard-head">
      <p className="wizard-eyebrow wizard-eyebrow--step">{eyebrow}</p>
      <h3 className="wizard-title" tabIndex={-1} ref={titleRef}>
        {title}
      </h3>
      <p className="wizard-intro">{intro}</p>
    </header>
  );

  return (
    <form
      className="wizard-shell"
      aria-labelledby={`${id}-heading`}
      onSubmit={(event) => {
        event.preventDefault();
        if (pending) return;
        if (showTracking) return;
        if (step < STEPS.length - 1) {
          advance();
          return;
        }
        const problem = validate(values, allowLocalCheckout);
        setInvalid(problem);
        // Values are deliberately never reset, so a recoverable failure does not
        // discard what the operator typed.
        if (!problem) onSubmit(toRequest(values));
      }}
    >
      <h2 className="onboard-sr" id={`${id}-heading`}>
        Collect a repository
      </h2>

      <nav className="wizard-rail" aria-label="Onboarding steps">
        <p className="wizard-eyebrow">Onboard a repository</p>
        <ol className="wizard-steps">
          {STEPS.map((definition, index) => {
            const done = showTracking || index < step;
            const active = !showTracking && index === step;
            return (
              <li key={definition.title}>
                <button
                  type="button"
                  className="wizard-step"
                  data-state={done ? "done" : active ? "active" : "pending"}
                  aria-current={active ? "step" : undefined}
                  disabled={pending || (!showTracking && index > step)}
                  onClick={() => goTo(index)}
                >
                  <span className="wizard-step__dot" aria-hidden="true">
                    {done ? "✓" : index + 1}
                  </span>
                  <span className="wizard-step__text">
                    <strong>{definition.title}</strong>
                    <small>{definition.detail}</small>
                  </span>
                </button>
                {index < STEPS.length - 1 && (
                  <span className="wizard-steps__line" aria-hidden="true" />
                )}
              </li>
            );
          })}
        </ol>
        <div className="wizard-rail__note">
          <p className="wizard-rail__note-label">Idempotent</p>
          <p>
            An equivalent request returns the same command, run and proposal —
            never a duplicate collection. No partial state ever publishes.
          </p>
        </div>
      </nav>

      <div className="wizard-main">
        {showTracking ? (
          <section className="wizard-panel" aria-label="Collection tracking">
            {stepHead(
              "Collection accepted",
              "Tracking the collection",
              "The command is durable — this panel follows it stage by stage. Resubmitting an equivalent request returns this same command.",
            )}
            <div className="wizard-tracking">{tracking}</div>
            <div className="wizard-nav">
              <button
                type="button"
                className="wizard-btn"
                onClick={() => goTo(STEPS.length - 1)}
              >
                Back to the form
              </button>
            </div>
          </section>
        ) : (
          <section className="wizard-panel" key={step}>
            {step === 0 &&
              stepHead(
                "Step 1 of 3",
                "Source",
                "Pin the exact source. Collection only ever runs against an immutable revision — branches and tags are rejected.",
              )}
            {step === 1 &&
              stepHead(
                "Step 2 of 3",
                "Analysis profile",
                <>
                  {repositoryTyped && revisionTyped ? (
                    <>
                      Defaults below are pre-filled for{" "}
                      <span className="wizard-ref">
                        {repositoryTyped} @ {revisionTyped.slice(0, 7)}
                      </span>
                      . Nothing is inspected until collection runs against the
                      pinned revision — everything is overridable.
                    </>
                  ) : (
                    <>
                      The profile below is pre-filled with defaults. Nothing is
                      inspected until collection runs against the pinned
                      revision — everything is overridable.
                    </>
                  )}
                </>,
              )}
            {step === 2 &&
              stepHead(
                "Step 3 of 3",
                "Review & collect",
                "Everything below is exactly what the collector receives. Submission is idempotent — an equivalent request returns the same command, run and proposal.",
              )}

            {step === 0 && (
              <fieldset className="wizard-fields" disabled={pending}>
                <legend className="onboard-sr">Source</legend>
                <p className="wizard-field">
                  <label htmlFor={`${id}-sourceType`}>Source mode</label>
                  <select
                    id={`${id}-sourceType`}
                    name="sourceType"
                    value={values.sourceType}
                    onChange={set("sourceType")}
                  >
                    <option value="GIT">Git repository URL</option>
                    {/* Rendered only under the development policy — production omits
                        the option entirely rather than disabling it. */}
                    {allowLocalCheckout && (
                      <option value="LOCAL_CHECKOUT">
                        Local development checkout
                      </option>
                    )}
                  </select>
                </p>
                {textField("origin", "Repository origin (HTTPS)", {
                  placeholder:
                    "https://github.com/spring-projects/spring-petclinic",
                  mono: true,
                })}
                <div className="wizard-grid2">
                  {textField("repository", "Repository name")}
                  {textField("system", "System")}
                </div>
                {textField("revision", "Exact commit (40 hex)", {
                  placeholder: "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
                  mono: true,
                })}
                {allowLocalCheckout && values.sourceType === "LOCAL_CHECKOUT"
                  ? textField("checkoutPath", "Development checkout path", {
                      mono: true,
                    })
                  : null}
              </fieldset>
            )}

            {step === 1 && (
              <fieldset className="wizard-fields" disabled={pending}>
                <legend className="onboard-sr">Analysis profile</legend>
                <div className="wizard-grid2">
                  {selectField("environment", "Environment", ENVIRONMENTS)}
                  {selectField(
                    "platform",
                    "Platform",
                    activePack.schemaProfiles,
                  )}
                </div>
                <div className="wizard-field wizard-field--pack">
                  <span className="wizard-field__labelrow">
                    <span className="wizard-field__label">Analyzer pack</span>
                    <span
                      className="wizard-pack-chip"
                      data-default={packIsDefault || undefined}
                    >
                      {packIsDefault ? "Default" : "Selected"}
                    </span>
                  </span>
                  <div className="wizard-pack">
                    <span className="wizard-pack__tile" aria-hidden="true">
                      {activePack.tile}
                    </span>
                    <span className="wizard-pack__text">
                      <strong>{activePack.pack}</strong>
                      <small>{activePack.summary}</small>
                    </span>
                    <button
                      type="button"
                      className="wizard-pack__change"
                      aria-expanded={packPickerOpen}
                      onClick={() => setPackPickerOpen((open) => !open)}
                    >
                      {packPickerOpen ? "Done" : "Change"}
                    </button>
                  </div>
                  {packPickerOpen && (
                    <select
                      className="wizard-pack__picker"
                      aria-label="Analyzer pack"
                      value={values.analyzerPack}
                      onChange={(event) => choosePack(event.target.value)}
                    >
                      {ANALYZER_PACKS.map((definition) => (
                        <option key={definition.pack} value={definition.pack}>
                          {definition.pack}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                <div className="wizard-grid2">
                  {selectField("ruleset", "Ruleset", RULESETS, true)}
                  {selectField(
                    "schemaProfile",
                    "Schema profile",
                    activePack.schemaProfiles,
                    true,
                  )}
                </div>
                <label className="wizard-toggle">
                  <input
                    type="checkbox"
                    className="onboard-sr"
                    checked
                    disabled
                    readOnly
                  />
                  <span className="wizard-toggle__switch" aria-hidden="true" />
                  <span className="wizard-toggle__text">
                    <strong>Derive element-level lineage</strong>
                    <small>
                      Column → field mapping where the analyzer can prove it
                    </small>
                  </span>
                  <span className="wizard-toggle__state">Always on</span>
                </label>
              </fieldset>
            )}

            {step === 2 && (
              <div className="wizard-review">
                <dl>
                  {reviewRows.map((row) => (
                    <div key={row.key}>
                      <dt>{row.key}</dt>
                      <dd>{row.value || "—"}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}

            <div className="wizard-nav">
              {step > 0 && (
                <button
                  type="button"
                  className="wizard-btn"
                  disabled={pending}
                  onClick={() => goTo(step - 1)}
                >
                  Back
                </button>
              )}
              <span className="wizard-nav__spacer" aria-hidden="true" />
              {step === 0 && (
                <button
                  type="button"
                  className="wizard-btn wizard-btn--primary"
                  disabled={pending}
                  onClick={advance}
                >
                  Continue to analysis profile
                  <span aria-hidden="true"> →</span>
                </button>
              )}
              {step === 1 && (
                <button
                  type="button"
                  className="wizard-btn wizard-btn--primary"
                  disabled={pending}
                  onClick={advance}
                >
                  Review &amp; collect<span aria-hidden="true"> →</span>
                </button>
              )}
              {step === 2 && (
                <button
                  type="submit"
                  className="wizard-btn wizard-btn--primary"
                  disabled={pending}
                >
                  {pending ? "Submitting collection…" : "Collect repository"}
                </button>
              )}
            </div>

            {message && (
              <p className="inline-error wizard-error" role="alert">
                {message}
              </p>
            )}
          </section>
        )}
      </div>

      <aside className="wizard-plan" aria-label="Collection plan">
        <p className="wizard-eyebrow wizard-eyebrow--plan">Collection plan</p>
        <dl className="wizard-plan__rows">
          {planRows.map((row) => (
            <div key={row.key}>
              <dt>{row.key}</dt>
              <dd data-empty={row.value ? undefined : true}>
                {row.value || "—"}
              </dd>
            </div>
          ))}
        </dl>
        <div className="wizard-plan__note">
          <p className="wizard-plan__note-label">Path · Chosen by the collector</p>
          <p>
            A first collection takes the <strong>baseline path</strong>. A known
            system automatically takes the <strong>incremental path</strong> and
            re-derives only what changed.
          </p>
        </div>
        <p className="wizard-plan__motto">
          Evidence before assertion —
          <br />
          no edge publishes without its receipt.
        </p>
      </aside>
    </form>
  );
}
