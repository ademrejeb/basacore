"use client";

import { useState } from "react";

import { CopyButton } from "@/components/copy-value";

export interface CodeSample {
  label: string;
  code: string;
  hint?: string;
}

export function CodeTabs({ samples }: { samples: CodeSample[] }) {
  const [active, setActive] = useState(0);
  const sample = samples[active];

  if (!sample) return null;

  return (
    <div className="code-tabs">
      <div className="code-tabs__bar">
        <div className="code-tabs__labels">
          {samples.map((item, index) => (
            <button
              key={item.label}
              type="button"
              className={`code-tabs__tab${index === active ? " is-active" : ""}`}
              onClick={() => setActive(index)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <CopyButton value={sample.code} label="Copy code" />
      </div>
      <pre className="code-block code-block--flush">{sample.code}</pre>
      {sample.hint ? <p className="copy-value__hint">{sample.hint}</p> : null}
    </div>
  );
}
