# macOS AXTree Payload

The AXTree payload is optional source-specific data. The macOS collector can
store it in `Observation.extraData.axtree` when it captures accessibility state.

AXTree data is useful for:

- Identifying focused text fields and buttons.
- Building stable `ActionTarget.elementPath` values.
- Supporting fuzzy matching when UI titles or positions change slightly.
- Debugging why a collector chose a specific subject or action.

```ts
export interface AXTreeSnapshot {
  app: string;

  bundleId?: string;

  windowTitle?: string;

  selectedText?: string;

  visibleTexts: string[];

  focusedElement?: {
    role: string;

    title?: string;

    value?: string;

    path?: string;
  };

  tree?: AXNode;
}

export interface AXNode {
  id: string;

  role: string;

  subrole?: string;

  title?: string;

  value?: string;

  description?: string;

  enabled?: boolean;

  focused?: boolean;

  selected?: boolean;

  frame?: {
    x: number;
    y: number;
    width: number;
    height: number;
  };

  children?: AXNode[];
}
```
