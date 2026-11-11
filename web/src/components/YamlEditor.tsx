// Author: @SaadZarook
import type { CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { redo, redoDepth, undo, undoDepth } from "@codemirror/commands";
import { yaml } from "@codemirror/lang-yaml";
import { EditorState } from "@codemirror/state";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef, useState } from "react";

/** Light-on-ink colours that keep the YAML readable on the dark editor (WCAG AA). */
const darkHighlight = HighlightStyle.define([
  { tag: tags.propertyName, color: "#8fb4ff" },
  { tag: [tags.string, tags.content], color: "#9be3b0" },
  { tag: [tags.number, tags.bool, tags.null, tags.keyword], color: "#f2c97d" },
  { tag: tags.comment, color: "#aab2bf" },
  { tag: [tags.meta, tags.punctuation, tags.separator], color: "#d6dae1" },
]);

interface YamlEditorProps {
  value: string;
  label: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  /** Names to suggest as you type, such as an entity's locations and their fields. */
  completions?: string[];
  /** The text as saved, when there is one: Revert to saved puts it back, as one step you can undo. */
  saved?: string | undefined;
}

/** The same text but for line endings, which the editor normalizes. */
const same = (a: string, b: string) => a.replace(/\r\n/g, "\n") === b.replace(/\r\n/g, "\n");

/** A CodeMirror 6 editor in YAML mode; the text lives in the parent, which stays the source of truth. */
export function YamlEditor({ value, label, onChange, readOnly = false, completions = [], saved }: YamlEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView>();
  const latest = useRef(onChange);
  // Read by the completion source on each keystroke, so a new entity's names need no new editor.
  const names = useRef(completions);
  // What Undo and Redo can do, kept from the editor's own history: Ctrl+Z, Ctrl+Y and the buttons share it.
  const [depths, setDepths] = useState({ undo: 0, redo: 0 });

  useEffect(() => {
    latest.current = onChange;
    names.current = completions;
  });

  /** The names that continue the word before the cursor: ledger for "count(led", record.status for "record.st". */
  const suggest = (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[A-Za-z_][\w.]*/);
    if (word === null || names.current.length === 0) return null;
    return {
      from: word.from,
      options: names.current.map((name) => ({ label: name, type: name.includes(".") ? "property" : "variable" })),
      validFor: /^[\w.]*$/,
    };
  };

  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      doc: value,
      parent: host.current,
      extensions: [
        basicSetup,
        yaml(),
        // basicSetup's autocompletion asks the language data for sources: this one adds the names.
        EditorState.languageData.of(() => [{ autocomplete: suggest }]),
        syntaxHighlighting(darkHighlight),
        EditorView.contentAttributes.of({ "aria-label": label }),
        EditorView.editable.of(!readOnly),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) latest.current?.(update.state.doc.toString());
          setDepths({ undo: undoDepth(update.state), redo: redoDepth(update.state) });
        }),
      ],
    });
    // A scrollable region must be keyboard reachable (axe: scrollable-region-focusable).
    editor.scrollDOM.tabIndex = 0;
    view.current = editor;
    return () => editor.destroy();
    // The editor is created once per label and mode; later text changes are dispatched below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [label, readOnly]);

  useEffect(() => {
    const editor = view.current;
    // A text that differs only in line endings is the same file: replacing it would only add an
    // undo step that changes nothing, so Ctrl+Z would seem to do nothing.
    if (editor && !same(editor.state.doc.toString(), value)) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
    }
  }, [value]);

  const run = (command: (target: EditorView) => boolean) => {
    const editor = view.current;
    if (editor) {
      command(editor);
      editor.focus();
    }
  };
  const revert = () => {
    const editor = view.current;
    if (editor && saved !== undefined) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: saved } });
      editor.focus();
    }
  };
  const changed = saved !== undefined && !same(value, saved);

  return (
    <div className="yaml-editor-wrap">
      {!readOnly && (
        <div className="yaml-toolbar" role="toolbar" aria-label="Edit history">
          <button className="btn btn-outline small" type="button" disabled={depths.undo === 0} onClick={() => run(undo)} title="Ctrl+Z">
            Undo
          </button>
          <button className="btn btn-outline small" type="button" disabled={depths.redo === 0} onClick={() => run(redo)} title="Ctrl+Y">
            Redo
          </button>
          {saved !== undefined && (
            <button className="btn btn-outline small" type="button" disabled={!changed} onClick={revert}>
              Revert to saved
            </button>
          )}
        </div>
      )}
      <div className="yaml-editor" ref={host} />
    </div>
  );
}
