"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";

type Props = {
  label: string;
  submitLabel: string;
  initial?: string;
  onSubmit: (name: string) => Promise<void>;
  onCancel?: () => void;
};

/** A one-field form for naming a supplier or batch. Shows the API's error message on failure. */
export function NameForm({ label, submitLabel, initial = "", onSubmit, onCancel }: Props) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await onSubmit(name);
      if (!initial) setName("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Something went wrong. Try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="name-form" onSubmit={submit}>
      <label>
        <span className="visually-hidden">{label}</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={label}
          maxLength={200}
          required
          aria-invalid={error ? true : undefined}
        />
      </label>
      <button type="submit" className="button primary" disabled={pending || !name.trim()}>
        {pending ? "Saving…" : submitLabel}
      </button>
      {onCancel && (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
