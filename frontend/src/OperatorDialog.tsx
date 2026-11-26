import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

// Keep the modal in the top layer through the closing transition.
export function OperatorDialog({
  open,
  dismiss,
  children,
}: {
  open: boolean;
  dismiss: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [present, setPresent] = useState(open);
  useLayoutEffect(() => {
    if (open) {
      setPresent(true);
      const node = dialog.current;
      if (node && !node.open) {
        if (node.showModal) node.showModal();
        else node.setAttribute("open", "");
      }
    } else if (present) {
      const timer = window.setTimeout(
        () => {
          dialog.current?.close?.();
          setPresent(false);
        },
        window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
          ? 0
          : 180,
      );
      return () => window.clearTimeout(timer);
    }
  }, [open, present]);
  if (!open && !present) return null;
  return (
    <dialog
      ref={dialog}
      className="operatorDialog"
      aria-labelledby="operator-title"
      data-closing={!open || undefined}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
    >
      {children}
    </dialog>
  );
}
