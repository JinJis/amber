"use client";

// Client-side access to the feature flags resolved server-side (lib/features.getFeatures). The root
// client component (Chat) wraps its tree in <FeaturesProvider>; any descendant reads useFeatures().

import { createContext, useContext, type ReactNode } from "react";
import type { Features } from "./features";

// Chat-first default (FLAG-1): both OFF unless the provider is given real flags. A component rendered
// outside a provider therefore sees the safe, minimal surface rather than accidentally enabling boards.
const FeaturesContext = createContext<Features>({ dashboard: false, alerts: false });

export function FeaturesProvider({ value, children }: { value: Features; children: ReactNode }) {
  return <FeaturesContext.Provider value={value}>{children}</FeaturesContext.Provider>;
}

export const useFeatures = (): Features => useContext(FeaturesContext);
