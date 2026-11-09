"use client";

import { useState } from "react";

type Props = {
  /** The button's text before confirming. */
  label: string;
  /** What the user is agreeing to, shown beside the confirm and cancel buttons. */
  question: string;
  /** The confirm button's text. */
  confirmLabel: string;
  onConfirm: () => void | Promise<void>;
  className?: string;
  disabled?: boolean;
};

/**
 * A button that asks for confirmation on the page itself.
 *
 * Browser dialogs (window.confirm) are silently blocked in some embedded browsers, which return
 * "cancel" without showing anything, so the action would do nothing at all.
 */
export function ConfirmButton({
  label,
  question,
  confirmLabel,
  onConfirm,
  className = "button",
  disabled = false,
}: Props) {
  const [asking, setAsking] = useState(false);

  if (!asking) {
    return (
      <button
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={question}>
      <span>{question}</span>
      <button
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => {
          setAsking(false);
          void onConfirm();
        }}
        autoFocus
      >
        {confirmLabel}
      </button>
      <button
        type="button"
        className={className.includes("link") ? "button link" : "button"}
        onClick={() => setAsking(false)}
      >
        Cancel
      </button>
    </span>
  );
}
