import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown } from "lucide-react";

type Props = {
  label: string;
  icon?: ReactNode;
  options: { id: string; name: string }[];
  value: string;
  onChange: (value: string) => void;
};

export function Dropdown({ label, icon, options, value, onChange }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const selected = options.find((option) => option.id === value);

  useEffect(() => {
    if (!open) return;
    const initial =
      list.current?.querySelector<HTMLButtonElement>(
        '[aria-selected="true"]',
      ) ?? list.current?.querySelector("button");
    initial?.focus();
    function outside(event: PointerEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  return (
    <div
      className="picker"
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <span className="pickerLabel">
        {icon}
        {label}
      </span>
      <div className="pickerControl">
        <button
          type="button"
          ref={trigger}
          aria-label={label + ": " + (selected?.name ?? "Select")}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? id : undefined}
          onClick={() => setOpen(!open)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              setOpen(true);
            }
          }}
        >
          {selected?.name ?? "Select"}
          <ChevronDown />
        </button>
        {open && (
          <ul
            id={id}
            ref={list}
            role="listbox"
            aria-label={label}
            className="pickerList"
            onKeyDown={(event) => {
              const buttons = Array.from(
                event.currentTarget.querySelectorAll("button"),
              );
              const index = buttons.indexOf(
                document.activeElement as HTMLButtonElement,
              );
              switch (event.key) {
                case "Escape":
                  close();
                  break;
                case "ArrowDown":
                  buttons[(index + 1) % buttons.length]?.focus();
                  break;
                case "ArrowUp":
                  buttons[
                    (index + buttons.length - 1) % buttons.length
                  ]?.focus();
                  break;
                case "Home":
                  buttons[0]?.focus();
                  break;
                case "End":
                  buttons.at(-1)?.focus();
                  break;
                default:
                  return;
              }
              event.preventDefault();
            }}
          >
            {options.map((option) => (
              <li key={option.id} role="presentation">
                <button
                  type="button"
                  role="option"
                  aria-selected={option.id === value}
                  tabIndex={-1}
                  onClick={() => {
                    close();
                    if (option.id !== value) onChange(option.id);
                  }}
                >
                  {option.name}
                  {option.id === value && <Check />}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
